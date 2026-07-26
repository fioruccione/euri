"""
Euri Pulse — bus afferente compatibile e lineage cognitiva additiva.

I sensi che Euri già possiede (presenza, vault, orologio) e la sua interocezione
(sogni, insight, consolidamento) emettono qui eventi tipizzati. Il bus contiene due
classi esplicite:

    telemetry   osservazione grezza; non è conoscenza e non autorizza azioni
    cognitive   transizione cognitiva con entità e causalità dichiarate

Initiative consuma un sottoinsieme stretto del bus. Il Cognitive Projector copia
soltanto gli eventi ``cognitive`` in una timeline replayabile e osservazionale:
non crea memorie, non promuove insight e non esegue azioni.

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

Campi envelope v2, additivi e ignorabili dai consumer legacy:

    schema_version    versione dell'envelope
    event_class       "telemetry" | "cognitive"
    producer          componente/loop che ha prodotto l'evento
    trace_id          percorso cognitivo condiviso
    causation_id      evento immediatamente causale, se noto
    logical_event_id  identità stabile di dominio, se disponibile
    entity_refs       entità coinvolte (memorie, insight, dream...)
    parent_refs       fonti/antenati dichiarati
    epistemic_before / epistemic_after
    experiment_version
    duration_ms

Disaccoppiamento chiave: la CATTURA è a piena fedeltà; il consumo osservazionale
e le eventuali policy comportamentali restano componenti separate. La presenza
di un evento nel bus non è, da sola, una decisione né una credenza.

Fail-open assoluto: il polso è un senso, non un organo vitale. Se fallisce, tace —
non deve MAI rompere il senso che lo emette né il loop in cui vive.
"""

import json
import time
import uuid

PULSE_STREAM = "euri:pulse"
_MAXLEN = 50000  # tetto morbido: teniamo la storia sensoriale recente, non infinita
PULSE_SCHEMA_VERSION = "2"
TELEMETRY_EVENT = "telemetry"
COGNITIVE_EVENT = "cognitive"

_PULSE_EMIT_ONCE_LUA = """
if redis.call('HGET', KEYS[2], 'pulse_sent') == '1' then
    return 0
end
local stream_id = redis.call(
    'XADD', KEYS[1], 'MAXLEN', '~', ARGV[1], '*',
    'sense', ARGV[2], 'source', ARGV[3], 'kind', ARGV[4],
    'payload', ARGV[5], 'salience', ARGV[6], 'ts', ARGV[7],
    'schema_version', ARGV[8], 'event_class', ARGV[9],
    'producer', ARGV[10], 'trace_id', ARGV[11],
    'causation_id', ARGV[12], 'logical_event_id', ARGV[13],
    'entity_refs', ARGV[14], 'parent_refs', ARGV[15],
    'epistemic_before', ARGV[16], 'epistemic_after', ARGV[17],
    'experiment_version', ARGV[18], 'duration_ms', ARGV[19]
)
redis.call('HSET', KEYS[2], 'pulse_sent', '1', 'pulse_stream_id', stream_id)
return 1
"""


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _refs(value) -> list:
    if value is None:
        return []
    if isinstance(value, (str, dict)):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _envelope_fields(
    *,
    sense,
    source,
    kind,
    payload,
    salience,
    event_class,
    producer,
    trace_id,
    causation_id,
    logical_event_id,
    entity_refs,
    parent_refs,
    epistemic_before,
    epistemic_after,
    experiment_version,
    duration_ms,
) -> dict[str, str]:
    return {
        "sense": str(sense),
        "source": str(source),
        "kind": str(kind),
        "payload": _json(payload or {}),
        "salience": f"{float(salience):.3f}",
        "ts": f"{time.time():.3f}",
        "schema_version": PULSE_SCHEMA_VERSION,
        "event_class": str(event_class or TELEMETRY_EVENT),
        "producer": str(producer or ""),
        "trace_id": str(trace_id or ""),
        "causation_id": str(causation_id or ""),
        "logical_event_id": str(logical_event_id or ""),
        "entity_refs": _json(_refs(entity_refs)),
        "parent_refs": _json(_refs(parent_refs)),
        "epistemic_before": str(epistemic_before or ""),
        "epistemic_after": str(epistemic_after or ""),
        "experiment_version": str(experiment_version or ""),
        "duration_ms": "" if duration_ms is None else f"{float(duration_ms):.3f}",
    }


def pulse_emit(
    r,
    sense,
    source,
    kind,
    payload=None,
    salience=0.5,
    *,
    event_class=TELEMETRY_EVENT,
    producer="",
    trace_id="",
    causation_id="",
    logical_event_id="",
    entity_refs=None,
    parent_refs=None,
    epistemic_before="",
    epistemic_after="",
    experiment_version="",
    duration_ms=None,
):
    """Emette un evento sul bus afferente e, se possibile, ritorna lo stream ID.

    Fail-open: qualunque errore restituisce ``None`` e non interrompe il produttore.
    """
    if r is None:
        return None
    try:
        import config
        if not getattr(config, "PULSE_ENABLED", True):
            return None
    except Exception:
        pass
    try:
        return r.xadd(
            PULSE_STREAM,
            _envelope_fields(
                sense=sense,
                source=source,
                kind=kind,
                payload=payload,
                salience=salience,
                event_class=event_class,
                producer=producer,
                trace_id=trace_id,
                causation_id=causation_id,
                logical_event_id=logical_event_id,
                entity_refs=entity_refs,
                parent_refs=parent_refs,
                epistemic_before=epistemic_before,
                epistemic_after=epistemic_after,
                experiment_version=experiment_version,
                duration_ms=duration_ms,
            ),
            maxlen=_MAXLEN,
            approximate=True,
        )
    except Exception:
        return None


def cognitive_emit(
    r,
    sense,
    source,
    kind,
    *,
    payload=None,
    salience=0.5,
    producer,
    trace_id="",
    causation_id="",
    logical_event_id="",
    entity_refs=None,
    parent_refs=None,
    epistemic_before="",
    epistemic_after="",
    experiment_version="",
    duration_ms=None,
):
    """Emette una transizione cognitiva, senza trasformarla in memoria.

    Se il chiamante non possiede ancora una trace, ne nasce una nuova. Il valore
    ritornato è lo stream ID Redis e può diventare ``causation_id`` dell'evento
    successivo.
    """
    trace_id = str(trace_id or uuid.uuid4())
    return pulse_emit(
        r,
        sense,
        source,
        kind,
        payload=payload,
        salience=salience,
        event_class=COGNITIVE_EVENT,
        producer=producer,
        trace_id=trace_id,
        causation_id=causation_id,
        logical_event_id=logical_event_id,
        entity_refs=entity_refs,
        parent_refs=parent_refs,
        epistemic_before=epistemic_before,
        epistemic_after=epistemic_after,
        experiment_version=experiment_version,
        duration_ms=duration_ms,
    )


def pulse_emit_once(
    r,
    event_id,
    sense,
    source,
    kind,
    payload=None,
    salience=0.5,
    marker_key: str | None = None,
    *,
    event_class=TELEMETRY_EVENT,
    producer="",
    trace_id="",
    causation_id="",
    entity_refs=None,
    parent_refs=None,
    epistemic_before="",
    epistemic_after="",
    experiment_version="",
    duration_ms=None,
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
        fields = _envelope_fields(
            sense=sense,
            source=source,
            kind=kind,
            payload=payload,
            salience=salience,
            event_class=event_class,
            producer=producer,
            trace_id=trace_id,
            causation_id=causation_id,
            logical_event_id=event_id,
            entity_refs=entity_refs,
            parent_refs=parent_refs,
            epistemic_before=epistemic_before,
            epistemic_after=epistemic_after,
            experiment_version=experiment_version,
            duration_ms=duration_ms,
        )
        r.eval(
            _PULSE_EMIT_ONCE_LUA,
            2,
            PULSE_STREAM,
            marker_key or f"euri:pulse:dedup:{event_id}",
            str(_MAXLEN),
            fields["sense"],
            fields["source"],
            fields["kind"],
            fields["payload"],
            fields["salience"],
            fields["ts"],
            fields["schema_version"],
            fields["event_class"],
            fields["producer"],
            fields["trace_id"],
            fields["causation_id"],
            fields["logical_event_id"],
            fields["entity_refs"],
            fields["parent_refs"],
            fields["epistemic_before"],
            fields["epistemic_after"],
            fields["experiment_version"],
            fields["duration_ms"],
        )
        return True
    except Exception:
        return False
