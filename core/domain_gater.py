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
from redis.commands.search.query import Query

import config


def _knn_domains(vec_bytes: bytes, r, k: int, exclude_id: str | None = None) -> list[str]:
    """
    Ritorna i domini delle k memorie semanticamente più vicine (KNN su tutto il DB),
    ordinati dal più vicino. Riferimento auto-derivato: nessun dominio è cablato nel
    codice, vengono letti a runtime dalla memoria stessa di Euri.
    `exclude_id` salta la memoria di partenza (utile in audit, quando è già nel DB).
    """
    try:
        n = k + (1 if exclude_id else 0)
        q = (
            Query(f"*=>[KNN {n} @embedding $vec AS score]")
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


def neighbor_domains(vec_bytes: bytes, r, k: int = 8) -> list[str]:
    """P1 — domini dei vicini semantici, da passare come suggerimento ad assign_domain."""
    return _knn_domains(vec_bytes, r, k)


def detect_domain_outlier(vec_bytes: bytes, assigned_domain: str, r,
                          k: int = 10, exclude_id: str | None = None) -> dict:
    """
    R1 — rileva se il dominio assegnato è incoerente col vicinato semantico.
    Outlier = il dominio assegnato NON compare tra i domini dei k vicini.
    Tutto auto-riferito ai dati di Euri: confronta la memoria con ciò che Euri
    stesso ha già imparato, non con una verità di dominio esterna.

    Ritorna: {is_outlier, assigned, suggested, neighbor_counts}
    """
    from collections import Counter
    doms = _knn_domains(vec_bytes, r, k, exclude_id=exclude_id)
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


def domain_aware_search(query: str, embedder, r, limit: int = 5) -> list[dict]:
    """
    Ricerca vettoriale in due passaggi.
    Prima cerca di capire il dominio della query, poi filtra i risultati in quel dominio.
    Se ci sono meno di 2 risultati, allarga al DB completo.
    """
    # 1. Capisci di che dominio stiamo cercando informazioni
    query_domain = assign_domain(query)
    vec = embedder.encode(query, mode="query")
    if vec is None:
        return []
    vec_bytes = vec.astype("float32").tobytes()

    # Gating come BOOST, non come filtro rigido. Si recupera un pool ampio
    # dall'INTERO DB (nessuna esclusione per dominio) e si ri-ordina dando una
    # spinta alle memorie nel dominio della query. Così un fatto molto pertinente
    # ma archiviato in un altro dominio (es. "grado 17→P-Pile" salvato in
    # 'logistica' mentre la query è taggata 'business') riemerge comunque, mentre
    # il dominio resta una preferenza — non più una museruola che causa falsi negativi.
    # L'anti-poisoning è ora coperto a monte dal Memory Guard sull'ingest.
    DOMAIN_BOOST = 0.85  # <1: 'score' è una distanza, quindi in-dominio = avvicinato
    pool = max(limit * 4, 20)
    try:
        q_all = (
            Query(f"*=>[KNN {pool} @embedding $vec AS score]")
            .sort_by("score")
            .return_fields("id", "content", "source", "score", "created_at", "category", "domain")
            .dialect(2)
        )
        res_all = r.ft("idx:memories").search(q_all, query_params={"vec": vec_bytes})
    except Exception as e:
        logger.error(f"Errore search full DB: {e}")
        return []

    items = []
    for doc in res_all.docs:
        try:
            raw_doc = r.json().get(doc.id, "$")
        except Exception:
            raw_doc = None
        if not raw_doc:
            continue
        item = raw_doc[0]
        # Idratazione piena: il path domain-boosted deve portarsi dietro anche
        # requires_verification/provenance_stale/audit_flag/consolidation_risk, altrimenti
        # il prompt RAG perde proprio i flag epistemici che guidano la cautela.
        if item.get("superseded_by"):
            continue
        dom = item.get("domain") or getattr(doc, "domain", "generale")
        raw = float(doc.score)
        in_domain = query_domain != "generale" and dom == query_domain
        item = dict(item)
        item["id"] = item.get("id") or doc.id.replace("euri:memory:", "")
        item["score"] = raw
        item["domain"] = dom
        item["_adj"] = raw * (DOMAIN_BOOST if in_domain else 1.0)
        items.append(item)

    items.sort(key=lambda x: x["_adj"])
    results = items[:limit]
    n_dom = sum(1 for it in results if it["domain"] == query_domain)
    for it in results:
        it.pop("_adj", None)
    logger.debug(
        f"Domain-boosted search: {len(results)} risultati "
        f"(dominio query '{query_domain}', {n_dom} in-dominio su pool {pool})"
    )
    return results
