"""
Domain Gating — assegnazione automatica e ricerca per domini "auto-scoperti".

L'LLM legge il contenuto da salvare e assegna un'etichetta semantica (es. "chimica", "moto").
In fase di recupero, la ricerca avviene in due step:
 1. Cerca il termine prima di tutto filtrando per lo stesso dominio (evita poisoning)
 2. Fallback all'intero DB se ci sono pochi risultati
"""
import ollama
from core.ollama_client import chat_client
from loguru import logger

from core.memory_risk import rank_memories_epistemically
from redis.commands.search.query import Query

import config
from core.memory_scope import current_scope, normalize_scope, scope_clause


def _json_get_many(r, keys) -> list:
    """Idrata un pool RedisJSON in pipeline, preservandone l'ordine."""
    keys = list(keys)
    if not keys:
        return []
    try:
        pipe = r.pipeline(transaction=False)
        for key in keys:
            pipe.json().get(key, "$")
        return pipe.execute()
    except Exception:
        out = []
        for key in keys:
            try:
                out.append(r.json().get(key, "$"))
            except Exception:
                out.append(None)
        return out


def _knn_domains(
    vec_bytes: bytes,
    r,
    k: int,
    exclude_id: str | None = None,
    memory_scope: str | None = None,
) -> list[str]:
    """
    Ritorna i domini delle k memorie semanticamente più vicine (KNN su tutto il DB),
    ordinati dal più vicino. Riferimento auto-derivato: nessun dominio è cablato nel
    codice, vengono letti a runtime dalla memoria stessa di Euri.
    `exclude_id` salta la memoria di partenza (utile in audit, quando è già nel DB).
    """
    try:
        n = k + (1 if exclude_id else 0)
        q = (
            Query(
                f"({scope_clause(memory_scope)})"
                f"=>[KNN {n} @embedding $vec AS score]"
            )
            .sort_by("score")
            .return_fields("id", "domain")
            .dialect(2)
        )
        res = r.ft("idx:memories").search(q, query_params={"vec": vec_bytes})
        out: list[str] = []
        for doc in res.docs:
            did = doc.id.replace("euri:memory:", "")
            if exclude_id and did == exclude_id:
                continue
            out.append(getattr(doc, "domain", "generale") or "generale")
        return out[:k]
    except Exception as e:
        logger.debug(f"Errore _knn_domains: {e}")
        return []


def neighbor_domains(
    vec_bytes: bytes,
    r,
    k: int = 8,
    *,
    memory_scope: str | None = None,
) -> list[str]:
    """P1 — domini dei vicini semantici, da passare come suggerimento ad assign_domain."""
    return _knn_domains(vec_bytes, r, k, memory_scope=memory_scope)


def detect_domain_outlier(
    vec_bytes: bytes,
    assigned_domain: str,
    r,
    k: int = 10,
    exclude_id: str | None = None,
    memory_scope: str | None = None,
) -> dict:
    """
    R1 — rileva se il dominio assegnato è incoerente col vicinato semantico.
    Outlier = il dominio assegnato NON compare tra i domini dei k vicini.
    Tutto auto-riferito ai dati di Euri: confronta la memoria con ciò che Euri
    stesso ha già imparato, non con una verità di dominio esterna.

    Ritorna: {is_outlier, assigned, suggested, neighbor_counts}
    """
    from collections import Counter
    doms = _knn_domains(
        vec_bytes,
        r,
        k,
        exclude_id=exclude_id,
        memory_scope=memory_scope,
    )
    if not doms:
        return {"is_outlier": False, "assigned": assigned_domain,
                "suggested": None, "neighbor_counts": {}}
    counts = Counter(doms)
    is_outlier = assigned_domain not in counts
    # Dominio proposto = modale tra i vicini, preferendo qualcosa di più specifico di "generale"
    ranked = [d for d, _ in counts.most_common() if d != "generale"]
    suggested = (ranked[0] if ranked else counts.most_common(1)[0][0])
    return {"is_outlier": is_outlier, "assigned": assigned_domain,
            "suggested": suggested, "neighbor_counts": dict(counts)}


def assign_domain(content: str, hint_domains: list[str] | None = None) -> str:
    """
    Chiede a Gemma di assegnare un dominio/argomento principale al contenuto.
    Se fallisce o è troppo generico, ritorna "generale".

    `hint_domains` (P1): domini dei vicini semantici, offerti come SUGGERIMENTO non
    vincolante. Servono a disambiguare frammenti corti (es. "neutri" → chimica, non
    nucleare) senza ingabbiare l'apprendimento: Euri resta libero di coniare un
    dominio nuovo se nessun suggerimento è pertinente.
    """
    hint_block = ""
    if hint_domains:
        uniq = list(dict.fromkeys(hint_domains))[:6]  # dedup, mantieni ordine di vicinanza
        hint_block = (
            "\nMemorie semanticamente simili sono già classificate con questi domini: "
            + ", ".join(f'"{d}"' for d in uniq)
            + ".\nSe UNO di questi è pertinente, riusalo ESATTAMENTE com'è scritto. "
            "Altrimenti assegna comunque il dominio corretto, anche se nuovo e non in elenco.\n"
        )

    prompt = f"""\
Leggi la seguente informazione e assegna UN'etichetta di dominio: una o due parole \
che descrivano l'ambito o l'argomento principale.

Esempi: "lavoro", "famiglia", "moto", "chimica polimeri", "stampaggio iniezione", \
"spesa", "programmazione", "salute", "elettronica", "business".
{hint_block}
Informazione: "{content}"

Rispondi SOLO con l'etichetta. Nessuna spiegazione. Niente virgolette. Tutto minuscolo."""

    try:
        response = chat_client.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.2, "num_predict": 30},
            think=False,
        )
        # Pulisce eventuale reasoning <think> come nel validator
        text = response.message.content or ""
        if "<channel|>" in text:
            text = text.split("<channel|>", 1)[-1]
        import re
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

        domain = text.strip().strip('"\'').lower()
        words = domain.split()

        # Rifiuta solo frasi (3+ parole) o stringhe vuote/troppo lunghe
        if not domain or len(domain) > 30 or len(words) > 2:
            return "generale"

        return domain
    except Exception as e:
        logger.debug(f"Errore assign_domain: {e}")
        return "generale"


def _source_prefilter(
    source_filter: list[str] | None,
    source_exclude: list[str] | None,
    memory_scope: str | None = None,
) -> str:
    clauses = [scope_clause(memory_scope)]
    if source_filter:
        clauses.append("@source:{" + "|".join(source_filter) + "}")
    if source_exclude:
        clauses.extend(f"-@source:{{{source}}}" for source in source_exclude)
    return " ".join(clauses) or "*"


def _vector_pool_cache_key(
    limit: int,
    source_filter: list[str] | None,
    source_exclude: list[str] | None,
    memory_scope: str | None,
) -> tuple:
    """Chiave effimera per un pool KNN: non viene mai serializzata."""
    return (
        int(limit),
        tuple(source_filter or ()),
        tuple(source_exclude or ()),
        normalize_scope(memory_scope or current_scope()),
    )


def _load_vector_pool(
    vec,
    r,
    limit: int,
    *,
    source_filter: list[str] | None = None,
    source_exclude: list[str] | None = None,
    memory_scope: str | None = None,
) -> list[dict] | None:
    """Legge e idrata il pool KNN senza assegnare il dominio o fare touch.

    Questa parte della ricerca dipende soltanto dal vettore e dai filtri. Puo'
    quindi essere anticipata sulla CPU/Redis mentre il semantic frame usa il
    modello conversazionale. Il boost di dominio e il ranking restano nel
    chiamante e mantengono la policy storica.
    """
    pool = max(int(limit) * 4, 20)
    memory_scope = normalize_scope(memory_scope or current_scope())
    try:
        prefilter = _source_prefilter(
            source_filter, source_exclude, memory_scope
        )
        q_all = (
            Query(f"({prefilter})=>[KNN {pool} @embedding $vec AS score]")
            .sort_by("score")
            .return_fields(
                "id", "content", "source", "score", "created_at",
                "category", "domain",
            )
            .dialect(2)
        )
        res_all = r.ft("idx:memories").search(
            q_all,
            query_params={"vec": vec.astype("float32").tobytes()},
        )
    except Exception as e:
        logger.error(f"Errore search full DB: {e}")
        return None

    items: list[dict] = []
    raw_docs = list(res_all.docs)
    hydrated = _json_get_many(r, (doc.id for doc in raw_docs))
    for doc, raw_doc in zip(raw_docs, hydrated):
        if not raw_doc:
            continue
        item = raw_doc[0]
        if item.get("superseded_by"):
            continue
        if normalize_scope(item.get("memory_scope")) != memory_scope:
            continue
        source = item.get("source")
        if source_filter is not None and source not in source_filter:
            continue
        if source_exclude is not None and source in source_exclude:
            continue
        row = dict(item)
        row["id"] = row.get("id") or doc.id.replace("euri:memory:", "")
        row["score"] = float(doc.score)
        row["domain"] = row.get("domain") or getattr(
            doc, "domain", "generale"
        )
        items.append(row)
    return items


def prefetch_domain_search(
    query: str,
    embedder,
    r,
    pool_specs: list[dict],
) -> dict:
    """Prepara vettore e pool read-only, senza chiamare alcun LLM.

    Il risultato usa lo stesso ``query_feature_cache`` consumato dal percorso
    sincrono. Un errore produce semplicemente una cache vuota: il chiamante
    eseguira' la ricerca storica.
    """
    cache: dict = {"entries": {}, "pools": {}, "prefetched": True}
    vec = embedder.encode(query, mode="query")
    if vec is None:
        return cache
    cache["entries"][str(query)] = {"vector": vec}
    for spec in pool_specs:
        key = _vector_pool_cache_key(
            spec["limit"],
            spec.get("source_filter"),
            spec.get("source_exclude"),
            spec.get("memory_scope"),
        )
        if key in cache["pools"]:
            continue
        pool = _load_vector_pool(
            vec,
            r,
            spec["limit"],
            source_filter=spec.get("source_filter"),
            source_exclude=spec.get("source_exclude"),
            memory_scope=spec.get("memory_scope"),
        )
        # Un errore transitorio non diventa un risultato vuoto autorevole: il
        # percorso sincrono deve poter ritentare con la policy storica.
        if pool is not None:
            cache["pools"][key] = pool
    return cache


def domain_aware_search(
    query: str,
    embedder,
    r,
    limit: int = 5,
    *,
    source_filter: list[str] | None = None,
    source_exclude: list[str] | None = None,
    memory_scope: str | None = None,
    query_feature_cache: dict | None = None,
) -> list[dict]:
    """
    Ricerca vettoriale in due passaggi.
    Prima cerca di capire il dominio della query, poi filtra i risultati in quel dominio.
    Se ci sono meno di 2 risultati, allarga al DB completo.
    """
    # 1. Capisci di che dominio stiamo cercando informazioni. Nel dual-channel
    # la medesima query alimenta due ricerche con filtri diversi: dominio ed
    # embedding sono proprietà della query, quindi possono essere condivisi
    # entro il singolo turno senza condividere risultati o ranking.
    cache_key = str(query)
    cache_entries = (
        query_feature_cache.setdefault("entries", {})
        if query_feature_cache is not None else {}
    )
    cached = (
        cache_entries.get(cache_key)
        if query_feature_cache is not None else None
    )
    if cached is not None:
        query_domain = cached.get("domain")
        vec = cached.get("vector")
        if query_domain is None:
            query_domain = assign_domain(query)
            cached["domain"] = query_domain
        if vec is None:
            vec = embedder.encode(query, mode="query")
            if vec is not None:
                cached["vector"] = vec
        query_feature_cache["hits"] = int(query_feature_cache.get("hits", 0)) + 1
    else:
        query_domain = assign_domain(query)
        vec = embedder.encode(query, mode="query")
        if query_feature_cache is not None and vec is not None:
            cache_entries[cache_key] = {
                "domain": query_domain,
                "vector": vec,
            }
    if vec is None:
        return []
    # Gating come BOOST, non come filtro rigido. Si recupera un pool ampio
    # dall'INTERO DB (nessuna esclusione per dominio) e si ri-ordina dando una
    # spinta alle memorie nel dominio della query. Così un fatto molto pertinente
    # ma archiviato in un altro dominio (es. "grado 17→P-Pile" salvato in
    # 'logistica' mentre la query è taggata 'business') riemerge comunque, mentre
    # il dominio resta una preferenza — non più una museruola che causa falsi negativi.
    # L'anti-poisoning è ora coperto a monte dal Memory Guard sull'ingest.
    DOMAIN_BOOST = 0.85  # <1: 'score' è una distanza, quindi in-dominio = avvicinato
    memory_scope = normalize_scope(memory_scope or current_scope())
    pool_key = _vector_pool_cache_key(
        limit, source_filter, source_exclude, memory_scope
    )
    cached_pools = (
        query_feature_cache.setdefault("pools", {})
        if query_feature_cache is not None else {}
    )
    if pool_key in cached_pools:
        items = [dict(item) for item in cached_pools[pool_key]]
        query_feature_cache["pool_hits"] = int(
            query_feature_cache.get("pool_hits", 0)
        ) + 1
    else:
        loaded = _load_vector_pool(
            vec,
            r,
            limit,
            source_filter=source_filter,
            source_exclude=source_exclude,
            memory_scope=memory_scope,
        )
        if loaded is None:
            return []
        items = loaded
        if query_feature_cache is not None:
            cached_pools[pool_key] = [dict(item) for item in items]

    for item in items:
        dom = item.get("domain") or "generale"
        raw = float(item["score"])
        in_domain = query_domain != "generale" and dom == query_domain
        item["_adj"] = raw * (DOMAIN_BOOST if in_domain else 1.0)

    items.sort(key=lambda x: x["_adj"])
    results = rank_memories_epistemically(items, limit=limit)
    n_dom = sum(1 for it in results if it["domain"] == query_domain)
    for it in results:
        it.pop("_adj", None)
    logger.debug(
        f"Domain-boosted search: {len(results)} risultati "
        f"(dominio query '{query_domain}', {n_dom} in-dominio su pool "
        f"{max(limit * 4, 20)})"
    )
    return results
