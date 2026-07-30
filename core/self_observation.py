"""
Loop 2h — Self-Observation.

Osserva le contraddizioni che il Loop 2f ha risolto (via superseded_by).
Prima di narrarle distingue semanticamente identità e somiglianza:

- SAME: produce una reflection di evoluzione;
- RELATED: ripristina le due entità distinte e dichiara la somiglianza sul pulse;
- DIFFERENT: ripristina le due entità senza inventare un ponte;
- UNKNOWN: fail-closed, nessuna modifica e nuovo tentativo futuro.

Coerente con paper §7h (autoconsapevolezza in atto) e §7i (tempo asimmetrico):
la traiettoria del pensiero diventa esplicita invece che nascosta dal
soft-delete. Il Loop 2f propone l'arco; il Loop 2h lo racconta soltanto se gli
estremi sono fatti primari ammissibili. Le reflection già generate dal Loop 2h
non possono alimentare nuove self-observation: la narrativa non diventa prova
di se stessa.

La classificazione è affidata al modello locale con un contratto JSON
domain-agnostic. La policy non accetta però il solo verdetto: richiede una base
esplicita e due referenti citati letteralmente dalle fonti. Un giudizio
incompleto, inferito o non verificabile diventa UNKNOWN per costruzione.
"""
import json
import re
import time
from collections import defaultdict
import httpx

import ollama
from core.ollama_client import get_dream_client
from core.memory_attention import update_loop2e_candidate_index
from loguru import logger

import config
from core.pulse import cognitive_emit


class SelfObservation:
    """Loop 2h — narrative di evoluzione dalle coppie superseded."""

    NARRATED_KEY = "euri:loop2h:narrated"
    NON_EVOLUTION_KEY = "euri:loop2h:non_evolution"
    MAX_PAIRS_PER_CYCLE = 10
    NARRATED_TTL_DAYS = 365
    OLLAMA_TIMEOUT_S = 200
    UNKNOWN_RETRY_BASE_DAYS = 1
    UNKNOWN_RETRY_MAX_DAYS = 30
    RELATION_CONTRACT_VERSION = "loop2h-evidenced-identity-v1"

    def __init__(self, r, memory_manager):
        self._r = r
        self._memory = memory_manager

    # ──────────────────────────────────────────
    # ENTRY POINT
    # ──────────────────────────────────────────

    def run(self, *, precommit_guard=None) -> dict:
        """
        Esegue il pass. Ritorna metriche {pairs_found, reflection_id}.
        Idempotente: ogni coppia viene narrata una sola volta (tracked via NARRATED_KEY).
        Se il mondo cambia durante la generazione, ``precommit_guard`` impedisce
        che una narrativa ormai stale venga pubblicata.
        """
        found_pairs = self._collect_unnarrated_pairs(
            limit=self.MAX_PAIRS_PER_CYCLE
        )
        if not found_pairs:
            logger.debug("Loop 2h: nessuna evoluzione nuova da raccontare")
            return {"pairs_found": 0, "reflection_id": None}

        pairs = []
        for pair in found_pairs:
            relation, note = self._classify_pair_relation(pair)
            if relation == "same":
                pairs.append(pair)
                continue
            if relation == "related":
                if not self._reverse_false_supersession(pair, relation, note):
                    logger.warning(
                        "Loop 2h: relazione RELATED non consumata; "
                        f"riparazione da ritentare — {pair['pair_key']}"
                    )
                    continue
                self._emit_related_pair(pair, note)
                self._mark_non_evolution(pair)
                continue
            if relation == "different":
                if not self._reverse_false_supersession(pair, relation, note):
                    logger.warning(
                        "Loop 2h: relazione DIFFERENT non consumata; "
                        f"riparazione da ritentare — {pair['pair_key']}"
                    )
                    continue
                self._mark_non_evolution(pair)
                logger.info(
                    "Loop 2h: coppia esclusa dall'evoluzione "
                    f"(entità diverse) — {pair['pair_key']}"
                )
                continue
            # UNKNOWN/error: fail-closed ma ritentabile. Non consumare la coppia.
            self._defer_unknown_pair(pair)
            logger.info(
                "Loop 2h: identità non risolta → nessuna narrativa "
                f"({pair['pair_key']})"
            )

        if not pairs:
            return {"pairs_found": len(found_pairs), "reflection_id": None}

        grouped = self._group_by_domain(pairs)
        narrative = self._generate_narrative(grouped)
        if not narrative:
            logger.warning("Loop 2h: generazione narrativa fallita o vuota")
            return {"pairs_found": len(found_pairs), "reflection_id": None}

        def _can_publish():
            if not self._pairs_still_valid(pairs):
                return False
            return precommit_guard is None or bool(precommit_guard())

        ref_id = self._save_as_reflection(
            narrative,
            pairs,
            precommit_guard=_can_publish,
        )
        if not ref_id:
            logger.info(
                "Loop 2h: reflection non pubblicata; coppie lasciate da narrare"
            )
            return {"pairs_found": len(found_pairs), "reflection_id": None}

        self._mark_narrated(pairs)
        parent_ids = self._pair_parent_ids(pairs)
        cognitive_emit(
            self._r,
            "reflection",
            "intero",
            "created",
            producer="loop2h",
            trace_id=f"reflection:{ref_id}",
            logical_event_id=f"reflection:{ref_id}",
            entity_refs=[{"type": "memory", "id": ref_id, "role": "child"}],
            parent_refs=parent_ids,
            payload={
                "id": ref_id,
                "source_memory_ids": parent_ids,
                "supersession_pairs": [
                    {
                        "loser_id": p["loser"].get("id"),
                        "winner_id": p["winner"].get("id"),
                    }
                    for p in pairs
                ],
            },
            epistemic_before="superseded_memory_pairs",
            epistemic_after="internal_self_observation_requires_verification",
            salience=0.4,
        )

        logger.success(
            f"Loop 2h: {len(pairs)} evoluzioni raccontate in reflection {ref_id[:8]}…"
        )
        return {"pairs_found": len(found_pairs), "reflection_id": ref_id}

    # ──────────────────────────────────────────
    # COLLECT
    # ──────────────────────────────────────────

    def _collect_unnarrated_pairs(self, limit: int) -> list[dict]:
        """
        Scan memorie superseded, skip quelle già narrate (set NARRATED_KEY).
        Carica vincitore + perdente con contenuti completi.
        """
        pairs: list[dict] = []
        seen_pair_keys: set[str] = set()
        for key in self._r.scan_iter("euri:memory:*"):
            if len(pairs) >= limit:
                break
            try:
                d = self._r.json().get(key, "$")
                if not d:
                    continue
                loser = d[0]
                winner_id = loser.get("superseded_by")
                if isinstance(winner_id, list):  # tollera entrambi i formati (str o [str])
                    winner_id = winner_id[0] if winner_id else None
                if not winner_id:
                    continue
                loser_id = loser.get("id")
                if not loser_id:
                    continue
                loser_rejection = self._evolution_endpoint_rejection_reason(
                    loser
                )
                if loser_rejection:
                    logger.debug(
                        "Loop 2h: estremo loser escluso "
                        f"({loser_rejection}) — {loser_id[:8]}"
                    )
                    continue

                pair_key = "|".join(sorted([loser_id, winner_id]))
                # Redis SCAN può restituire la stessa chiave più volte durante
                # un'iterazione. Una coppia è una sola evoluzione, non due.
                if pair_key in seen_pair_keys:
                    continue
                if self._r.sismember(self.NARRATED_KEY, pair_key):
                    continue
                if self._r.sismember(self.NON_EVOLUTION_KEY, pair_key):
                    continue
                deferred = loser.get("loop2h_identity_deferred")
                if isinstance(deferred, dict):
                    deferred_winner = str(
                        deferred.get("winner_id") or ""
                    )
                    try:
                        retry_at = float(deferred.get("next_retry_at") or 0)
                    except (TypeError, ValueError):
                        retry_at = 0
                    if deferred_winner == str(winner_id) and retry_at > time.time():
                        continue

                w_raw = self._r.json().get(f"euri:memory:{winner_id}", "$")
                if not w_raw:
                    continue
                winner = w_raw[0]
                winner_rejection = self._evolution_endpoint_rejection_reason(
                    winner
                )
                if winner_rejection:
                    logger.debug(
                        "Loop 2h: estremo winner escluso "
                        f"({winner_rejection}) — {str(winner_id)[:8]}"
                    )
                    continue

                pairs.append({
                    "loser": loser,
                    "winner": winner,
                    "pair_key": pair_key,
                })
                seen_pair_keys.add(pair_key)
            except Exception as e:
                logger.debug(f"Loop 2h: skip {key} — {e}")
                continue
        return pairs

    def _defer_unknown_pair(self, pair: dict) -> bool:
        """Rinvia un'identità irrisolta senza consumarla o bloccare la coda.

        Il backoff è legato allo specifico arco loser→winner. Se 2f crea un
        nuovo vincitore, il vecchio rinvio non si applica. Errori di scrittura
        non cambiano l'arco e non trasformano UNKNOWN in una decisione.
        """
        loser_id = str(pair.get("loser", {}).get("id") or "")
        winner_id = str(pair.get("winner", {}).get("id") or "")
        if not loser_id or not winner_id:
            return False
        previous = pair["loser"].get("loop2h_identity_deferred")
        attempts = 0
        if (
            isinstance(previous, dict)
            and str(previous.get("winner_id") or "") == winner_id
        ):
            try:
                attempts = max(0, int(previous.get("attempts") or 0))
            except (TypeError, ValueError):
                attempts = 0
        attempts += 1
        delay_days = min(
            self.UNKNOWN_RETRY_MAX_DAYS,
            self.UNKNOWN_RETRY_BASE_DAYS * (2 ** min(attempts - 1, 10)),
        )
        now_ts = time.time()
        deferred = {
            "winner_id": winner_id,
            "attempts": attempts,
            "last_attempt_at": now_ts,
            "next_retry_at": now_ts + delay_days * 86400,
            "retry_delay_days": delay_days,
            "relation_audit": dict(
                pair.get("loop2h_relation_audit") or {}
            ),
            "producer": "loop2h",
        }
        try:
            self._r.json().set(
                f"euri:memory:{loser_id}",
                "$.loop2h_identity_deferred",
                deferred,
            )
            pair["loser"]["loop2h_identity_deferred"] = deferred
            return True
        except Exception as exc:
            logger.warning(
                "Loop 2h: rinvio UNKNOWN non persistito "
                f"({pair.get('pair_key')}) — {exc}"
            )
            return False

    @staticmethod
    def _evolution_endpoint_rejection_reason(doc: dict) -> str | None:
        """Blocca input derivati o già giudicati contaminati.

        Una self-observation è una narrativa derivata: può essere recuperata in
        conversazione, ma non deve diventare premessa di un'altra
        self-observation. Questo evita ricorsioni in cui una vecchia conflazione
        viene riscritta con tono sempre più plausibile.
        """
        if not doc:
            return "missing_document"
        from core.memory_scope import PERSONAL_SCOPE, scope_of
        if scope_of(doc) != PERSONAL_SCOPE:
            return "non_personal_scope"

        verification = str(doc.get("verification_status") or "").casefold()
        epistemic = str(doc.get("epistemic_status") or "").casefold()
        if verification == "rejected_cross_entity_evolution":
            return "rejected_cross_entity_evolution"
        if epistemic == "cross_entity_conflation":
            return "cross_entity_conflation"

        tags = doc.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        tagset = {str(tag).casefold() for tag in tags}
        is_loop2h_reflection = (
            doc.get("source") == "reflection"
            and (
                "loop2h" in tagset
                or "self_observation" in tagset
                or bool(doc.get("self_observation_pairs"))
            )
        )
        if is_loop2h_reflection:
            return "recursive_self_observation"
        return None

    def _classify_pair_relation(self, pair: dict) -> tuple[str, str]:
        """Giudizio semantico SAME/RELATED/DIFFERENT con prova verificabile.

        SAME permette una narrativa di evoluzione. RELATED riconosce una
        somiglianza utile tra entità diverse e la pubblica soltanto come pulse.
        UNKNOWN/errore/giudizio soltanto inferito è fail-closed e resta
        ritentabile. Il metodo annota ``pair["loop2h_relation_audit"]`` per
        rendere ispezionabile la decisione downstream.
        """
        old = (pair["loser"].get("content") or "").strip()
        new = (pair["winner"].get("content") or "").strip()
        if not old or not new:
            pair["loop2h_relation_audit"] = {
                "contract_version": self.RELATION_CONTRACT_VERSION,
                "accepted_relation": "unknown",
                "reason": "missing_content",
            }
            return "unknown", ""
        prompt = f"""\
Confronta due memorie. Devi distinguere IDENTITÀ da SOMIGLIANZA e devi
giustificare l'identità con testo presente nelle fonti.

MEMORIA PRIMA: "{old[:900]}"
MEMORIA DOPO: "{new[:900]}"

Prima identifica il CLAIM SUBJECT di ciascuna memoria: l'entità della quale la
memoria registra una proprietà, uno stato o un impegno che potrebbe essere
aggiornato.
- claim_subject_a e claim_subject_b devono essere ESTRATTI LETTERALI brevi
  presenti nelle rispettive memorie, non parafrasi;
- non scegliere il nuovo valore della proprietà come soggetto. In "per il
  progetto Nadir la responsabile è Paola", il claim subject è "progetto Nadir",
  non Paola. In "la scheda corregge la formulazione F-88", il claim subject è
  "formulazione F-88", non la scheda. In "la procedura corregge il setpoint
  dell'estrusore E7", il claim subject è "estrusore E7", non la procedura;
- il claim subject è il soggetto logico di ciò che la memoria afferma, non un
  oggetto, componente o proprietà nominata incidentalmente. In "X usa il
  modulo Y" il claim subject è X, non il modulo Y;
- subject_specificity è SPECIFIC soltanto per un nome, codice o descrizione che
  individua inequivocabilmente il soggetto. Descrizioni come "il lotto pilota", "il
  cliente tedesco", "la macchina principale" o "il campione blu" sono GENERIC;
- basis è EXPLICIT soltanto quando il testo basta a stabilire l'identità.
  Se lo stesso nome non qualificato potrebbe indicare una persona, un progetto,
  un prodotto o un'organizzazione diversi, il tipo non è dimostrato: usa
  INSUFFICIENT e identity UNKNOWN. Non trasformare una differenza apparente di
  tipo in prova di entità distinte. Se devi affidarti al dominio o alla
  plausibilità, usa INSUFFICIENT.

Poi classifica identity:
- SAME: è esplicito che le due memorie riguardano lo stesso referente specifico;
- DISTINCT: è esplicito che riguardano referenti distinti;
- UNKNOWN: il testo non consente di decidere con certezza.

Se identity è DISTINCT, related_if_distinct vale YES soltanto se esiste una
somiglianza tecnica o operativa concreta; altrimenti NO. Se identity non è
DISTINCT usa NOT_APPLICABLE.

Rispondi con un oggetto JSON:
{{"identity":"SAME|DISTINCT|UNKNOWN",
  "basis":"EXPLICIT|INFERRED|INSUFFICIENT",
  "claim_subject_a":"estratto letterale dalla memoria prima",
  "claim_subject_b":"estratto letterale dalla memoria dopo",
  "subject_specificity_a":"SPECIFIC|GENERIC|UNKNOWN",
  "subject_specificity_b":"SPECIFIC|GENERIC|UNKNOWN",
  "entity_type_a":"PERSON|ORGANIZATION|PROJECT|MATERIAL|BATCH|MACHINE|DOCUMENT_OR_ORDER|EVENT_OR_COMMITMENT|PLACE|OTHER|UNKNOWN",
  "entity_type_b":"PERSON|ORGANIZATION|PROJECT|MATERIAL|BATCH|MACHINE|DOCUMENT_OR_ORDER|EVENT_OR_COMMITMENT|PLACE|OTHER|UNKNOWN",
  "related_if_distinct":"YES|NO|UNKNOWN|NOT_APPLICABLE",
  "note":"solo se DISTINCT+YES: una frase prudente sulla somiglianza; altrimenti stringa vuota"}}"""
        try:
            response = get_dream_client(self.OLLAMA_TIMEOUT_S).chat(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0, "num_predict": 800},
                think=False,
                format="json",
            )
            raw = re.sub(
                r"<think>.*?</think>",
                "",
                response.message.content or "",
                flags=re.DOTALL,
            ).strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start < 0 or end <= start:
                pair["loop2h_relation_audit"] = {
                    "contract_version": self.RELATION_CONTRACT_VERSION,
                    "accepted_relation": "unknown",
                    "reason": "missing_json_object",
                }
                return "unknown", ""
            data = json.loads(raw[start:end + 1])
            relation, note, audit = self._apply_relation_policy(data, old, new)
            pair["loop2h_relation_audit"] = audit
            return relation, note
        except (httpx.TimeoutException, TimeoutError):
            logger.warning(
                f"Loop 2h: timeout classificazione relazione dopo "
                f"{self.OLLAMA_TIMEOUT_S}s"
            )
            pair["loop2h_relation_audit"] = {
                "contract_version": self.RELATION_CONTRACT_VERSION,
                "accepted_relation": "unknown",
                "reason": "timeout",
            }
            return "unknown", ""
        except Exception as exc:
            logger.debug(f"Loop 2h: classificazione relazione fallita: {exc}")
            pair["loop2h_relation_audit"] = {
                "contract_version": self.RELATION_CONTRACT_VERSION,
                "accepted_relation": "unknown",
                "reason": f"classification_error:{type(exc).__name__}",
            }
            return "unknown", ""

    @staticmethod
    def _normalized_excerpt(value: object) -> str:
        """Normalizzazione minima per verificare una citazione senza semantica."""
        return " ".join(str(value or "").casefold().split()).strip("\"'“”‘’")

    @classmethod
    def _apply_relation_policy(
        cls,
        data: object,
        old: str,
        new: str,
    ) -> tuple[str, str, dict]:
        """Converte il giudizio strutturato in un'azione conservativa.

        Il modello propone; questa funzione autorizza. SAME e DISTINCT sono
        accettati soltanto con base esplicita, referenti specifici e citazioni
        verificabili nelle rispettive fonti. La policy non prova che il modello
        abbia ragione, ma impedisce che una label nuda o un'inferenza non
        ispezionabile modifichi lo stato della memoria.
        """
        version = cls.RELATION_CONTRACT_VERSION
        if not isinstance(data, dict):
            return "unknown", "", {
                "contract_version": version,
                "accepted_relation": "unknown",
                "reason": "json_not_object",
            }

        allowed_types = {
            "person",
            "organization",
            "project",
            "material",
            "batch",
            "machine",
            "document_or_order",
            "event_or_commitment",
            "place",
            "other",
            "unknown",
        }
        identity = str(data.get("identity") or "").strip().casefold()
        basis = str(data.get("basis") or "").strip().casefold()
        specificity_a = str(
            data.get("subject_specificity_a") or ""
        ).strip().casefold()
        specificity_b = str(
            data.get("subject_specificity_b") or ""
        ).strip().casefold()
        entity_type_a = str(data.get("entity_type_a") or "").strip().casefold()
        entity_type_b = str(data.get("entity_type_b") or "").strip().casefold()
        related = str(
            data.get("related_if_distinct") or ""
        ).strip().casefold()
        referent_a = " ".join(
            str(data.get("claim_subject_a") or "").split()
        )[:240]
        referent_b = " ".join(
            str(data.get("claim_subject_b") or "").split()
        )[:240]
        note = " ".join(str(data.get("note") or "").split())[:700]

        audit = {
            "contract_version": version,
            "proposed_identity": identity or "invalid",
            "basis": basis or "invalid",
            "claim_subject_a": referent_a,
            "claim_subject_b": referent_b,
            "subject_specificity_a": specificity_a or "invalid",
            "subject_specificity_b": specificity_b or "invalid",
            "entity_type_a": entity_type_a or "invalid",
            "entity_type_b": entity_type_b or "invalid",
            "related_if_distinct": related or "invalid",
            "accepted_relation": "unknown",
        }

        if (
            identity not in {"same", "distinct", "unknown"}
            or basis not in {"explicit", "inferred", "insufficient"}
            or specificity_a not in {"specific", "generic", "unknown"}
            or specificity_b not in {"specific", "generic", "unknown"}
            or entity_type_a not in allowed_types
            or entity_type_b not in allowed_types
            or related not in {"yes", "no", "unknown", "not_applicable"}
        ):
            audit["reason"] = "invalid_contract_value"
            return "unknown", "", audit

        if identity == "unknown":
            audit["reason"] = "model_unknown"
            return "unknown", "", audit
        if basis != "explicit":
            audit["reason"] = "identity_not_explicit"
            return "unknown", "", audit
        if specificity_a != "specific" or specificity_b != "specific":
            audit["reason"] = "referent_not_specific"
            return "unknown", "", audit

        normalized_a = cls._normalized_excerpt(referent_a)
        normalized_b = cls._normalized_excerpt(referent_b)
        source_a = cls._normalized_excerpt(old)
        source_b = cls._normalized_excerpt(new)
        if (
            not normalized_a
            or not normalized_b
            or normalized_a not in source_a
            or normalized_b not in source_b
        ):
            audit["reason"] = "unverifiable_source_excerpt"
            return "unknown", "", audit
        if entity_type_a == "unknown" or entity_type_b == "unknown":
            audit["reason"] = "unknown_entity_type"
            return "unknown", "", audit

        if identity == "same":
            if related != "not_applicable":
                audit["reason"] = "same_with_distinct_relation"
                return "unknown", "", audit
            if entity_type_a != entity_type_b:
                audit["reason"] = "same_with_type_mismatch"
                return "unknown", "", audit
            audit["accepted_relation"] = "same"
            audit["reason"] = "explicit_same_identity"
            return "same", "", audit

        # DISTINCT deve essere positivamente sostenuto. Due estratti identici
        # con lo stesso tipo descrivono al massimo un possibile omonimo: senza
        # un disambiguatore ulteriore restano UNKNOWN.
        if normalized_a == normalized_b and entity_type_a == entity_type_b:
            audit["reason"] = "indistinguishable_referents"
            return "unknown", "", audit
        if related == "yes":
            if not note:
                audit["reason"] = "related_without_note"
                return "unknown", "", audit
            audit["accepted_relation"] = "related"
            audit["reason"] = "explicit_distinct_related"
            return "related", note, audit
        if related == "no":
            audit["accepted_relation"] = "different"
            audit["reason"] = "explicit_distinct_unrelated"
            return "different", "", audit
        audit["reason"] = "distinct_relation_unresolved"
        return "unknown", "", audit

    def _emit_related_pair(self, pair: dict, note: str) -> None:
        """Dichiara sul pulse una somiglianza, senza creare identità o supersessione."""
        loser_id = str(pair["loser"].get("id") or "")
        winner_id = str(pair["winner"].get("id") or "")
        cognitive_emit(
            self._r,
            "memory_relation",
            "coppia",
            "comparison_noted",
            producer="loop2h",
            trace_id=f"memory-comparison:{pair['pair_key']}",
            logical_event_id=f"memory-comparison:{pair['pair_key']}",
            entity_refs=[
                {"type": "memory", "id": loser_id, "role": "compared"},
                {"type": "memory", "id": winner_id, "role": "compared"},
            ],
            parent_refs=[loser_id, winner_id],
            payload={
                "relation": "related_not_same",
                "note": note,
                "source_memory_ids": [loser_id, winner_id],
            },
            epistemic_before="suspected_supersession",
            epistemic_after="comparison_hypothesis",
            salience=0.45,
        )
        logger.info(
            "Loop 2h: somiglianza tra entità diverse dichiarata sul pulse — "
            f"{note[:160]}"
        )

    def _reverse_false_supersession(
        self,
        pair: dict,
        relation: str,
        note: str,
    ) -> bool:
        """Rende nuovamente visibile il loser se l'arco univa entità diverse.

        È una riparazione reversibile: nessun documento viene cancellato. L'arco
        precedente e il giudizio semantico restano annotati per l'audit.
        """
        loser_id = str(pair["loser"].get("id") or "")
        winner_id = str(pair["winner"].get("id") or "")
        if not loser_id or not winner_id:
            return False
        key = f"euri:memory:{loser_id}"
        try:
            current_raw = self._r.json().get(key, "$.superseded_by") or []
            current = current_raw[0] if current_raw else None
            if isinstance(current, list):
                current = current[0] if current else None
            if str(current or "") != winner_id:
                return False

            reversed_at = time.time()
            reversal_audit = {
                "previous_winner_id": winner_id,
                "reason": f"semantic_relation_{relation}",
                "note": note,
                "relation_audit": dict(
                    pair.get("loop2h_relation_audit") or {}
                ),
                "reversed_at": reversed_at,
                "producer": "loop2h",
                "committed": False,
            }
            # Due fasi: l'audit precede la modifica canonica. Se il processo si
            # interrompe, resta visibile che la riparazione era incompleta e la
            # coppia non viene consumata dal chiamante.
            self._r.json().set(
                key,
                "$.supersession_reversed",
                reversal_audit,
            )
            self._r.json().set(key, "$.superseded_by", None)
            reversal_audit["committed"] = True
            try:
                self._r.json().set(
                    key,
                    "$.supersession_reversed",
                    reversal_audit,
                )
            except Exception as exc:
                # L'arco errato è già stato rimosso: non ripristinarlo. Il
                # record committed=False lasciato dalla prima fase rende
                # comunque diagnosticabile l'interruzione.
                logger.warning(
                    "Loop 2h: arco riparato ma audit finale incompleto "
                    f"({pair['pair_key']}) — {exc}"
                )
            refreshed = dict(pair["loser"])
            refreshed["superseded_by"] = None
            refreshed["supersession_reversed"] = reversal_audit
            update_loop2e_candidate_index(self._r, refreshed)
            cognitive_emit(
                self._r,
                "memory_relation",
                "intero",
                "supersession_reversed",
                producer="loop2h",
                trace_id=f"memory-comparison:{pair['pair_key']}",
                logical_event_id=(
                    f"supersession-reversed:{pair['pair_key']}"
                ),
                entity_refs=[
                    {"type": "memory", "id": loser_id, "role": "restored"},
                    {
                        "type": "memory",
                        "id": winner_id,
                        "role": "previous_winner",
                    },
                ],
                parent_refs=[loser_id, winner_id],
                payload={
                    "loser_id": loser_id,
                    "previous_winner_id": winner_id,
                    "relation": relation,
                    "note": note,
                    "relation_audit": dict(
                        pair.get("loop2h_relation_audit") or {}
                    ),
                },
                epistemic_before="incorrect_supersession",
                epistemic_after="distinct_memories_restored",
                salience=0.6,
            )
            logger.info(
                "Loop 2h: supersessione tra entità diverse invertita "
                f"(reversibile) — {loser_id[:8]}… → {winner_id[:8]}…"
            )
            return True
        except Exception as exc:
            logger.warning(
                f"Loop 2h: impossibile invertire supersessione "
                f"{pair['pair_key']} — {exc}"
            )
            return False

    def _mark_non_evolution(self, pair: dict) -> None:
        self._r.sadd(self.NON_EVOLUTION_KEY, pair["pair_key"])
        self._r.expire(
            self.NON_EVOLUTION_KEY, self.NARRATED_TTL_DAYS * 86400
        )

    def _pairs_still_valid(self, pairs: list[dict]) -> bool:
        """Verifica al commit che ogni arco loser→winner esista ancora."""
        try:
            for pair in pairs:
                loser_id = str(pair["loser"].get("id") or "")
                winner_id = str(pair["winner"].get("id") or "")
                if not loser_id or not winner_id:
                    return False
                raw = self._r.json().get(f"euri:memory:{loser_id}", "$")
                if not raw:
                    return False
                current_winner = raw[0].get("superseded_by")
                if isinstance(current_winner, list):
                    current_winner = current_winner[0] if current_winner else None
                if str(current_winner or "") != winner_id:
                    return False
                if not self._r.json().get(f"euri:memory:{winner_id}", "$"):
                    return False
            return True
        except Exception:
            return False

    @staticmethod
    def _pair_parent_ids(pairs: list[dict]) -> list[str]:
        """Restituisce loser e winner unici, preservando l'ordine narrativo."""
        parent_ids: list[str] = []
        seen: set[str] = set()
        for pair in pairs:
            for role in ("loser", "winner"):
                memory_id = str(pair[role].get("id") or "")
                if memory_id and memory_id not in seen:
                    seen.add(memory_id)
                    parent_ids.append(memory_id)
        return parent_ids

    @staticmethod
    def _group_by_domain(pairs: list[dict]) -> dict[str, list[dict]]:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for p in pairs:
            domain = (
                p["winner"].get("domain")
                or p["loser"].get("domain")
                or "generale"
            )
            grouped[domain].append(p)
        return dict(grouped)

    # ──────────────────────────────────────────
    # GENERATE
    # ──────────────────────────────────────────

    def _generate_narrative(self, grouped: dict[str, list[dict]]) -> str:
        """Genera la riflessione in prima persona usando il modello dream (Qwen)."""
        sections = []
        for domain, pairs in grouped.items():
            section = f"## Dominio: {domain}\n"
            for i, p in enumerate(pairs, 1):
                old = (p["loser"].get("content") or "")[:240]
                new = (p["winner"].get("content") or "")[:240]
                section += (
                    f"{i}. PRIMA credevo: {old}\n"
                    f"   ORA credo:    {new}\n"
                )
            sections.append(section)

        prompt = (
            "Sono Euri, un sistema cognitivo con memoria persistente. Durante "
            "i cicli notturni il mio modulo di risoluzione contraddizioni ha "
            "rilevato che alcune mie memorie passate sono state superate da "
            "osservazioni più recenti. NON sono errori da cancellare — sono "
            "evoluzione del mio pensiero, e voglio raccontarmela in prima "
            "persona, onestamente.\n\n"
            "Ecco le evoluzioni nuove, raggruppate per dominio:\n\n"
            f"{chr(10).join(sections)}\n\n"
            "Scrivi una BREVE riflessione (max 200 parole) in prima persona "
            "che racconti queste evoluzioni come parte naturale del mio "
            "imparare. Distingui — dove è chiaro — se si tratta di un cambio "
            "di opinione, di una precisazione, o di un contesto diverso. "
            "Non elencare meccanicamente: fai emergere il senso della "
            "traiettoria. Non scusarti, non drammatizzare. Riconosci e basta."
        )

        try:
            response = get_dream_client(self.OLLAMA_TIMEOUT_S).chat(
                model=config.DREAM_OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}],
                # num_predict 3000: Qwen con think=True consuma reasoning
                # prima dell'output (lezione già appresa da generate_reflection
                # in V2.16). Cap basso → output troncato dentro <think>.
                options={"temperature": 0.6, "num_predict": 3000},
                think=True,
            )
            raw = (response.message.content or "").strip()
            # Qwen può lasciare <think>...</think>: rimuovo per pulizia output.
            text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            if not text:
                logger.debug(
                    f"Loop 2h: output vuoto post-strip — raw_len={len(raw)}, "
                    f"raw_head={raw[:200]!r}"
                )
            return text
        except (httpx.TimeoutException, TimeoutError):
            logger.warning(f"Loop 2h: timeout LLM dopo {self.OLLAMA_TIMEOUT_S}s")
            return ""
        except Exception as e:
            logger.error(f"Loop 2h: errore generazione narrativa: {e}")
            return ""

    # ──────────────────────────────────────────
    # SAVE
    # ──────────────────────────────────────────

    def _save_as_reflection(
        self,
        content: str,
        pairs: list[dict],
        *,
        precommit_guard=None,
    ) -> str | None:
        """Salva la narrativa come reflection con tag self_observation."""
        tags = ["self_observation", "loop2h", "evolution"]
        # Tagga i primi 5 pair_key (audit trail, evita esplosione tag)
        for p in pairs[:5]:
            tags.append(f"pair:{p['pair_key'][:16]}")

        parent_ids = self._pair_parent_ids(pairs)
        mid = self._memory.save_memory(
            content=content,
            category="riflessione",
            source="reflection",
            tags=tags,
            final_fields={
                "requires_verification": True,
                "epistemic_status": "internal_self_observation",
                "verification_status": "narrative_derived_from_supersession",
                "source_memory_ids": parent_ids,
                "self_observation_pairs": [
                    {
                        "loser_id": p["loser"].get("id"),
                        "winner_id": p["winner"].get("id"),
                        "pair_key": p["pair_key"],
                        "relation_audit": dict(
                            p.get("loop2h_relation_audit") or {}
                        ),
                    }
                    for p in pairs
                ],
            },
            precommit_guard=precommit_guard,
        )
        if mid:
            try:
                doc = self._r.json().get(f"euri:memory:{mid}", "$")
                domain = doc[0].get("domain", "generale") if doc else "generale"
                self._memory.supersede_duplicate_reflections(mid, domain, content)
            except Exception as e:
                logger.debug(f"Loop 2h reflection dedup error: {e}")
        return mid

    def _mark_narrated(self, pairs: list[dict]) -> None:
        """Aggiunge le pair_key al set NARRATED, con refresh TTL."""
        if not pairs:
            return
        for p in pairs:
            self._r.sadd(self.NARRATED_KEY, p["pair_key"])
        self._r.expire(self.NARRATED_KEY, self.NARRATED_TTL_DAYS * 86400)
