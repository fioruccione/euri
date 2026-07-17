"""
Euri Pulse — bus afferente (Fase 0: osservazione pura).

I sensi che Euri GIÀ possiede (presenza, vault, orologio) e la sua interocezione
(sogni, insight, consolidamento) emettono qui un evento tipizzato. NESSUNO consuma
ancora questo stream per agire: è un punto di osservazione, non un arco riflesso.
"Vediamo dove ci portano i segnali" prima di decidere cosa, semmai, Euri debba farne.

Contratto d'evento (envelope), volutamente generico — il bus NON sa cosa c'è nel
payload, così qualsiasi stimolo futuro entra senza toccare il bus:

    sense     stringa libera: "presence" | "vault" | "clock" | "dream" | "insight" | ...
    source    "extero" (il mondo fuori) | "intero" (Euri che sente sé stessa)
    kind      tipo di evento dentro il senso: "arrival" | "change" | "threshold" | ...
    payload   dict opaco al bus, ricco quanto serve
    salience  0.0–1.0, ipotesi GREZZA del senso su quanto "spicca". Fase 0: la
              registriamo per VEDERE se è un segnale utile; nessuno la legge ancora.
              (Decisione differita: se la salience la debba mettere il senso o uno
              strato a valle è proprio ciò che questi dati ci aiuteranno a decidere.)
    ts        epoch float

Disaccoppiamento chiave: la CATTURA è a piena fedeltà (è solo uno stream, costa
niente), il CONSUMO è separato e per ora assente. Quando la cognizione di Euri
crescerà, erediterà una storia sensoriale già ricca invece di ripartire da zero.

Fail-open assoluto: il polso è un senso, non un organo vitale. Se fallisce, tace —
non deve MAI rompere il senso che lo emette né il loop in cui vive.
"""

import json
import time

PULSE_STREAM = "euri:pulse"
_MAXLEN = 50000  # tetto morbido: teniamo la storia sensoriale recente, non infinita

_PULSE_EMIT_ONCE_LUA = """
if redis.call('HGET', KEYS[2], 'pulse_sent') == '1' then
    return 0
end
redis.call(
    'XADD', KEYS[1], 'MAXLEN', '~', ARGV[1], '*',
    'sense', ARGV[2], 'source', ARGV[3], 'kind', ARGV[4],
    'payload', ARGV[5], 'salience', ARGV[6], 'ts', ARGV[7]
)
redis.call('HSET', KEYS[2], 'pulse_sent', '1')
return 1
"""


def pulse_emit(r, sense, source, kind, payload=None, salience=0.5):
    """Emette un evento sul bus afferente. Non solleva mai."""
    if r is None:
        return
    try:
        import config
        if not getattr(config, "PULSE_ENABLED", True):
            return
    except Exception:
        pass
    try:
        r.xadd(
            PULSE_STREAM,
            {
                "sense": str(sense),
                "source": str(source),
                "kind": str(kind),
                "payload": json.dumps(payload or {}, ensure_ascii=False, default=str),
                "salience": f"{float(salience):.3f}",
                "ts": f"{time.time():.3f}",
            },
            maxlen=_MAXLEN,
            approximate=True,
        )
    except Exception:
        pass


def pulse_emit_once(
    r,
    event_id,
    sense,
    source,
    kind,
    payload=None,
    salience=0.5,
    marker_key: str | None = None,
) -> bool:
    """Emette un evento una sola volta; False indica che il retry deve restare pendente."""
    if r is None:
        return False
    try:
        import config
        if not getattr(config, "PULSE_ENABLED", True):
            return True
    except Exception:
        pass
    try:
        r.eval(
            _PULSE_EMIT_ONCE_LUA,
            2,
            PULSE_STREAM,
            marker_key or f"euri:pulse:dedup:{event_id}",
            str(_MAXLEN),
            str(sense),
            str(source),
            str(kind),
            json.dumps(payload or {}, ensure_ascii=False, default=str),
            f"{float(salience):.3f}",
            f"{time.time():.3f}",
        )
        return True
    except Exception:
        return False
