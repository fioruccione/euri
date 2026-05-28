"""
Tool Registry — Layer 2 intent routing semantico via VectorSet di Redis 8.8.

Sostituisce l'LLM classifier (~800ms su Gemma 4) con KNN nativo (~5ms) per
le query non ambigue. Le query con score sotto soglia ricadono sull'LLM
classifier esistente (Slow Path), che resta intatto come fallback.

Schema dati su Redis:
    euri:tools:vset       VectorSet (uno solo) — element=slug, vec=embedding
                          della descrizione + esempi.
    euri:tool:{slug}      JSON con metadata (intent, name, description,
                          examples, config dell'handler).

Kill switch: config.TOOL_VECTORSET_ENABLED=False disattiva il Fast Path.
Rollback dati: DEL euri:tools:vset + DEL euri:tool:* per pulire completamente.

Compatibilità: non rimpiazza il Layer 1 regex (resta sopra) né l'LLM
classifier (resta sotto come fallback). Modulo additivo, isolato.
"""
import json
import time

import numpy as np
import redis
from loguru import logger

import config


class ToolRegistry:
    """Catalogo dei tool indicizzato semanticamente, KNN via Redis 8.8 VectorSet."""

    VSET_KEY = "euri:tools:vset"
    JSON_PREFIX = "euri:tool:"
    DEFAULT_THRESHOLD = config.TOOL_VECTORSET_THRESHOLD
    DEFAULT_TOP_K = 3
    # Gap minimo tra top-1 e top-2 per considerare il match "deciso".
    # Senza gap, top-1 e top-2 sono nel rumore (multilingual-e5-large ha
    # baseline cosine ~0.85-0.92 su qualunque coppia di frasi italiane).
    MIN_GAP_TOP1_TOP2 = 0.005

    def __init__(self, r: redis.Redis, embedder, vset_key: str = None, json_prefix: str = None):
        """
        vset_key/json_prefix overridabili per test isolato (es. 'test:tools:vset').
        """
        self._r = r
        self._embedder = embedder
        self._vset_key = vset_key or self.VSET_KEY
        self._json_prefix = json_prefix or self.JSON_PREFIX

    # ──────────────────────────────────────────
    # REGISTRY CRUD
    # ──────────────────────────────────────────

    def register(
        self,
        slug: str,
        intent: str,
        name: str,
        description: str,
        examples: list[str] = None,
        config_dict: dict = None,
        is_fallback: bool = False,
    ) -> bool:
        """
        Registra un tool. Idempotente: se slug esiste, aggiorna in-place.
        Embedding = description + examples concatenati (più contesto = più
        precisione su query naturali).

        is_fallback=True marca il tool come "catch-all semantico": quando vince
        in match_tool(), restituisce None invece dell'intent → caller fa
        fallback. Utile per assorbire query generiche (es. 'chat') contro la
        baseline cosine alta di e5-large.

        Ritorna True se la registrazione è andata a buon fine.
        """
        if not self._embedder or not self._embedder.available:
            logger.error(f"ToolRegistry.register({slug}): embedder non disponibile")
            return False

        examples = examples or []
        config_dict = config_dict or {}

        # Embedding su description + examples (più segnale semantico)
        text_to_embed = description
        if examples:
            text_to_embed += "\nEsempi: " + " | ".join(examples)
        vec = self._embedder.encode(text_to_embed, mode="passage")
        if vec is None:
            logger.error(f"ToolRegistry.register({slug}): embedding fallito")
            return False

        now_ts = time.time()
        doc = {
            "slug": slug,
            "intent": intent,
            "name": name,
            "description": description,
            "examples": examples,
            "config": config_dict,
            "is_fallback": is_fallback,
            "created_at": now_ts,
            "updated_at": now_ts,
        }
        # Se esiste già, preserva created_at
        existing = self._r.json().get(f"{self._json_prefix}{slug}", "$")
        if existing:
            doc["created_at"] = existing[0].get("created_at", now_ts)

        self._r.json().set(f"{self._json_prefix}{slug}", "$", doc)

        # VADD su VectorSet — se l'element esiste già, viene aggiornato (idempotente)
        try:
            vec_bytes = np.asarray(vec, dtype=np.float32).tobytes()
            # Se esiste, rimuovi prima per evitare ambiguità di update
            self._r.execute_command("VREM", self._vset_key, slug)
            self._r.execute_command("VADD", self._vset_key, "FP32", vec_bytes, slug)
        except Exception as e:
            logger.error(f"ToolRegistry.register({slug}): VADD fallito: {e}")
            return False

        logger.debug(f"ToolRegistry: registrato '{slug}' → intent {intent}")
        return True

    def get(self, slug: str) -> dict | None:
        d = self._r.json().get(f"{self._json_prefix}{slug}", "$")
        return d[0] if d else None

    def list_all(self) -> list[dict]:
        out = []
        for key in self._r.scan_iter(f"{self._json_prefix}*"):
            d = self._r.json().get(key, "$")
            if d:
                out.append(d[0])
        return out

    def remove(self, slug: str) -> bool:
        try:
            self._r.execute_command("VREM", self._vset_key, slug)
        except Exception as e:
            logger.debug(f"ToolRegistry.remove({slug}): VREM (forse non c'era): {e}")
        deleted = self._r.delete(f"{self._json_prefix}{slug}")
        return deleted > 0

    def clear_all(self) -> int:
        """
        Cleanup completo: rimuove VectorSet + tutti i JSON tool.
        Usato in test e per rollback dati.
        Ritorna numero di tool cancellati.
        """
        n = 0
        for key in self._r.scan_iter(f"{self._json_prefix}*"):
            self._r.delete(key)
            n += 1
        self._r.delete(self._vset_key)
        return n

    # ──────────────────────────────────────────
    # ROUTING — la parte calda
    # ──────────────────────────────────────────

    def match_tool(
        self,
        query: str,
        threshold: float = None,
        top_k: int = None,
    ) -> dict | None:
        """
        KNN search via VSIM. Restituisce il tool top-1 SE score ≥ threshold,
        altrimenti None (caller userà fallback LLM).

        Ritorna dict:
            {
                "slug": str,
                "intent": str,
                "score": float,         # cosine similarity, 1.0 = identico
                "elapsed_ms": float,    # solo Redis KNN, escluso embedding
                "top_k": [(slug, score), ...]  # per debug
            }
        """
        if not self._embedder or not self._embedder.available:
            return None

        threshold = threshold if threshold is not None else self.DEFAULT_THRESHOLD
        top_k = top_k if top_k is not None else self.DEFAULT_TOP_K

        # Encode (NOTA: tempo embedding NON conteggiato in elapsed_ms — è costo
        # del query path, separato dal "lookup KNN")
        vec = self._embedder.encode(query, mode="query")
        if vec is None:
            return None

        # VSIM call
        try:
            t0 = time.time()
            vec_bytes = np.asarray(vec, dtype=np.float32).tobytes()
            raw = self._r.execute_command(
                "VSIM", self._vset_key, "FP32", vec_bytes,
                "COUNT", top_k, "WITHSCORES"
            )
            elapsed_ms = (time.time() - t0) * 1000
        except Exception as e:
            logger.error(f"ToolRegistry.match_tool: VSIM fallito: {e}")
            return None

        # Parse risposta: lista alternata [element1, score1, element2, score2, ...]
        if not raw or len(raw) < 2:
            return None

        # redis-py può restituire bytes — normalizza a str/float
        pairs = []
        for i in range(0, len(raw), 2):
            elem = raw[i].decode() if isinstance(raw[i], bytes) else raw[i]
            score = float(raw[i + 1].decode() if isinstance(raw[i + 1], bytes) else raw[i + 1])
            pairs.append((elem, score))

        best_slug, best_score = pairs[0]

        # Gate 1: threshold assoluta
        if best_score < threshold:
            return None

        # Gate 2: gap minimo top1-top2 (anti-ambiguità contro baseline alta e5-large)
        if len(pairs) >= 2:
            gap = pairs[0][1] - pairs[1][1]
            if gap < self.MIN_GAP_TOP1_TOP2:
                logger.debug(
                    f"ToolRegistry: ambiguità top1/top2 gap={gap:.4f} < "
                    f"{self.MIN_GAP_TOP1_TOP2} → fallback "
                    f"({pairs[0][0]}={pairs[0][1]:.3f} vs {pairs[1][0]}={pairs[1][1]:.3f})"
                )
                return None

        # Carica metadata del tool top-1
        meta = self.get(best_slug)
        if not meta:
            logger.warning(f"ToolRegistry.match_tool: VSIM trova {best_slug} ma JSON mancante")
            return None

        # Gate 3: catch-all semantico (es. 'chat') → fallback
        if meta.get("is_fallback"):
            logger.debug(f"ToolRegistry: match su catch-all {best_slug} → fallback")
            return None

        return {
            "slug": best_slug,
            "intent": meta["intent"],
            "score": best_score,
            "elapsed_ms": elapsed_ms,
            "top_k": pairs,
        }

    # ──────────────────────────────────────────
    # BOOTSTRAP
    # ──────────────────────────────────────────

    def bootstrap_from_definitions(self, definitions: list[dict]) -> int:
        """
        Carica il catalogo iniziale. `definitions` è una lista di dict
        {slug, intent, name, description, examples, config, is_fallback}.
        Restituisce numero di tool registrati con successo.
        """
        ok = 0
        for d in definitions:
            if self.register(
                slug=d["slug"],
                intent=d["intent"],
                name=d["name"],
                description=d["description"],
                examples=d.get("examples"),
                config_dict=d.get("config"),
                is_fallback=d.get("is_fallback", False),
            ):
                ok += 1
        return ok


# ──────────────────────────────────────────
# CATALOGO TOOL DEFAULT — i 6 intent del Layer 2
#
# Descrizioni derivate dal prompt LLM in core/llm_classifier.py:_PROMPT
# per garantire coerenza semantica col comportamento corrente.
# CHAT NON è incluso: è il default fallback, non ha senso indicizzarlo.
# ──────────────────────────────────────────

DEFAULT_TOOL_DEFINITIONS = [
    {
        "slug": "web_search",
        "intent": "WEB_SEARCH",
        "name": "Ricerca su internet",
        "description": (
            "L'utente vuole cercare informazioni su INTERNET, esplicitamente. "
            "Tipico quando menziona: online, web, internet, Google, sito di un'azienda, "
            "fatturato, notizie recenti, prezzi correnti, dati di un'azienda esterna."
        ),
        "examples": [
            "cerca su google il fatturato di Primpex",
            "fai una ricerca nel web per il prezzo del polietilene",
            "trovami online le quotazioni delle materie plastiche",
            "cerca su internet che cosa fa l'azienda Plastica Alto Sele",
        ],
        "config": {},
    },
    {
        "slug": "search",
        "intent": "SEARCH",
        "name": "Ricerca nelle memorie interne",
        "description": (
            "L'utente vuole che Euri cerchi nelle SUE MEMORIE INTERNE (Redis), "
            "non sul web. Tipico quando chiede ricordi, conversazioni passate, "
            "fatti già archiviati. Parole chiave: ricordi, in memoria, ti ricordi, "
            "stavamo parlando, cosa sai di."
        ),
        "examples": [
            "ricordi quando abbiamo parlato di Realube 5014",
            "hai in memoria qualcosa su Fanti Plast",
            "di cosa stavamo parlando ieri",
            "cerca i ricordi della settimana scorsa",
            "cosa sai di Plastica Alto Sele",
        ],
        "config": {},
    },
    {
        "slug": "save_todo",
        "intent": "SAVE_TODO",
        "name": "Salva un promemoria",
        "description": (
            "L'utente vuole ricordarsi di FARE qualcosa in futuro — task, "
            "promemoria, appuntamento. Parole chiave: devo fare, ricordami di, "
            "segna che devo, prossima settimana fare."
        ),
        "examples": [
            "ricordami di chiamare Mario domani mattina",
            "segna che devo controllare il lotto 03 PPR 043",
            "devo inviare il preventivo a Isi Plast entro venerdì",
        ],
        "config": {},
    },
    {
        "slug": "save_memory",
        "intent": "SAVE_MEMORY",
        "name": "Salva un fatto in memoria",
        "description": (
            "L'utente vuole che Euri ricordi un FATTO, non un task. "
            "Parole chiave: ricordati che, segna che, tieni a mente che, "
            "annota che, da ora in poi sappi che."
        ),
        "examples": [
            "ricordati che il grado 50 di Regrado PP è migliore del 25 con questa mescola",
            "segna che Primpex è prospect di trading occasionale",
            "tieni a mente che il fornitore consegna il martedì",
        ],
        "config": {},
    },
    {
        "slug": "execute",
        "intent": "EXECUTE",
        "name": "Comando hardware/sistema",
        "description": (
            "Richiesta ESPLICITA di lettura dello stato hardware o software "
            "della workstation Linux che ospita Euri. La frase DEVE menzionare "
            "esplicitamente uno di: cpu, ram, gpu, scheda video, disco, spazio "
            "su disco, processi attivi, uptime, log di sistema, temperatura "
            "hardware. NON è EXECUTE: commenti riflessivi su temi tecnici "
            "(fisica dei materiali, chimica, polimeri, processi industriali) "
            "— quelli sono conversazione."
        ),
        "examples": [
            "controlla la cpu",
            "quanta ram sto usando ora",
            "spazio libero sul disco fisso",
            "leggi il log di sistema",
            "che processi sono attivi sulla workstation",
            "stato della gpu",
            "uptime del server",
        ],
        "config": {},
    },
    {
        "slug": "complete",
        "intent": "COMPLETE",
        "name": "Dichiara compito completato",
        "description": (
            "L'utente dichiara di aver APPENA COMPLETATO un compito specifico, "
            "in modo diretto e breve. NON è COMPLETE: narrazioni lunghe di "
            "ciò che è successo durante la giornata, racconti tecnici dettagliati."
        ),
        "examples": [
            "l'ho fatto",
            "ho chiamato Mario",
            "ho inviato la mail al cliente",
            "ho pagato la fattura",
            "completato",
        ],
        "config": {},
    },
    {
        # Catch-all semantico per assorbire la baseline alta di e5-large.
        # Non è un intent operativo: quando vince, match_tool() restituisce
        # None, il caller fa fallback all'LLM classifier (che a sua volta
        # dirà CHAT o uno dei sei intent operativi).
        "slug": "chat",
        "intent": "CHAT",
        "name": "Conversazione generica (catch-all)",
        "description": (
            "Conversazione, dialogo aperto, scambio non-operativo. Include: "
            "saluti, opinioni, riflessioni filosofiche, domande sullo stato "
            "d'animo, ringraziamenti, battute, frasi di apertura/chiusura, "
            "ma anche COMMENTI RIFLESSIVI SU TEMI TECNICI già discussi "
            "(fisica, chimica, polimeri, processi, lavoro) — quando l'utente "
            "non sta chiedendo di fare un'azione, ma sta partecipando alla "
            "conversazione con un'osservazione o una considerazione. Niente "
            "azione operativa da eseguire."
        ),
        "examples": [
            "come stai oggi",
            "buongiorno Euri",
            "che ne pensi del lavoro che facciamo",
            "interessante quello che dici sulla fisica dei materiali",
            "mi piace il modo in cui affronti questa cosa dei polimeri",
            "hai centrato il punto sulla reologia",
            "ho capito, grazie",
            "ma davvero",
            "non lo so, vediamo",
            "ti volevo solo dire ciao",
        ],
        "config": {},
        "is_fallback": True,
    },
]
