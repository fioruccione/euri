"""
Loop 2h — Self-Observation.

Osserva le contraddizioni che il Loop 2f ha risolto (via superseded_by) e
produce una memoria reflection narrativa in prima persona che presenta
quelle contraddizioni come *evoluzioni* di pensiero, non come errori
cancellati.

Coerente con paper §7h (autoconsapevolezza in atto) e §7i (tempo asimmetrico):
la traiettoria del pensiero diventa esplicita invece che nascosta dal
soft-delete. Il Loop 2f continua a fare il suo lavoro (rimuovere dal
retrieval); il Loop 2h aggiunge la voce: «ecco come sto cambiando idea».

V1: produce una sola reflection per pass che riassume tutte le evoluzioni
nuove (mai narrate prima). Niente classificazione error/evolution/context
— quella è materia di V2.
"""
import re
from collections import defaultdict
import httpx

import ollama
from core.ollama_client import get_dream_client
from loguru import logger

import config


class SelfObservation:
    """Loop 2h — narrative di evoluzione dalle coppie superseded."""

    NARRATED_KEY = "euri:loop2h:narrated"
    MAX_PAIRS_PER_CYCLE = 10
    NARRATED_TTL_DAYS = 365
    OLLAMA_TIMEOUT_S = 200

    def __init__(self, r, memory_manager):
        self._r = r
        self._memory = memory_manager

    # ──────────────────────────────────────────
    # ENTRY POINT
    # ──────────────────────────────────────────

    def run(self) -> dict:
        """
        Esegue il pass. Ritorna metriche {pairs_found, reflection_id}.
        Idempotente: ogni coppia viene narrata una sola volta (tracked via NARRATED_KEY).
        """
        pairs = self._collect_unnarrated_pairs(limit=self.MAX_PAIRS_PER_CYCLE)
        if not pairs:
            logger.debug("Loop 2h: nessuna evoluzione nuova da raccontare")
            return {"pairs_found": 0, "reflection_id": None}

        grouped = self._group_by_domain(pairs)
        narrative = self._generate_narrative(grouped)
        if not narrative:
            logger.warning("Loop 2h: generazione narrativa fallita o vuota")
            return {"pairs_found": len(pairs), "reflection_id": None}

        ref_id = self._save_as_reflection(narrative, pairs)
        self._mark_narrated(pairs)

        logger.success(
            f"Loop 2h: {len(pairs)} evoluzioni raccontate in reflection {ref_id[:8]}…"
        )
        return {"pairs_found": len(pairs), "reflection_id": ref_id}

    # ──────────────────────────────────────────
    # COLLECT
    # ──────────────────────────────────────────

    def _collect_unnarrated_pairs(self, limit: int) -> list[dict]:
        """
        Scan memorie superseded, skip quelle già narrate (set NARRATED_KEY).
        Carica vincitore + perdente con contenuti completi.
        """
        pairs: list[dict] = []
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

                pair_key = "|".join(sorted([loser_id, winner_id]))
                if self._r.sismember(self.NARRATED_KEY, pair_key):
                    continue

                w_raw = self._r.json().get(f"euri:memory:{winner_id}", "$")
                if not w_raw:
                    continue
                winner = w_raw[0]

                pairs.append({
                    "loser": loser,
                    "winner": winner,
                    "pair_key": pair_key,
                })
            except Exception as e:
                logger.debug(f"Loop 2h: skip {key} — {e}")
                continue
        return pairs

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

    def _save_as_reflection(self, content: str, pairs: list[dict]) -> str:
        """Salva la narrativa come reflection con tag self_observation."""
        tags = ["self_observation", "loop2h", "evolution"]
        # Tagga i primi 5 pair_key (audit trail, evita esplosione tag)
        for p in pairs[:5]:
            tags.append(f"pair:{p['pair_key'][:16]}")

        mid = self._memory.save_memory(
            content=content,
            category="meta",
            source="reflection",
            tags=tags,
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
