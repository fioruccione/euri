"""
Domain Gating — assegnazione automatica e ricerca per domini "auto-scoperti".

L'LLM legge il contenuto da salvare e assegna un'etichetta semantica (es. "chimica", "moto").
In fase di recupero, la ricerca avviene in due step:
 1. Cerca il termine prima di tutto filtrando per lo stesso dominio (evita poisoning)
 2. Fallback all'intero DB se ci sono pochi risultati
"""
import ollama
from loguru import logger
from redis.commands.search.query import Query

import config


def assign_domain(content: str) -> str:
    """
    Chiede a Gemma di assegnare un dominio/argomento principale al contenuto.
    Se fallisce o è troppo generico, ritorna "generale".
    """
    prompt = f"""\
Leggi la seguente informazione e assegna UN'etichetta di dominio: una o due parole \
che descrivano l'ambito o l'argomento principale.

Esempi: "lavoro", "famiglia", "moto", "chimica polimeri", "stampaggio iniezione", \
"spesa", "programmazione", "salute", "elettronica", "business".

Informazione: "{content}"

Rispondi SOLO con l'etichetta. Nessuna spiegazione. Niente virgolette. Tutto minuscolo."""

    try:
        response = ollama.chat(
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

    results = []
    
    # 2. Passo 1: Ricerca filtrata nel dominio (Gated Search)
    if query_domain != "generale":
        try:
            # Sostituisce gli spazi con * per query di RediSearch flessibili se il dominio è di 2 parole
            safe_domain = query_domain.replace(" ", "\\ ")
            
            q_domain = (
                Query(f"(@domain:{{{safe_domain}}})=>[KNN {limit} @embedding $vec AS score]")
                .sort_by("score")
                .return_fields("id", "content", "source", "score", "created_at", "category", "domain")
                .dialect(2)
            )
            res_domain = r.ft("idx:memories").search(q_domain, query_params={"vec": vec_bytes})
            
            for doc in res_domain.docs:
                item = {
                    "id": doc.id,
                    "content": doc.content,
                    "score": float(doc.score),
                    "source": doc.source,
                    "domain": getattr(doc, "domain", "generale")
                }
                results.append(item)
                
            if len(results) >= 2:
                logger.debug(f"Domain Gating: trovati {len(results)} risultati nel dominio '{query_domain}'")
                return results
                
        except Exception as e:
            logger.debug(f"Errore search dominio '{query_domain}': {e}")
            
    # 3. Passo 2: Fallback all'intero DB
    try:
        q_all = (
            Query(f"*=>[KNN {limit} @embedding $vec AS score]")
            .sort_by("score")
            .return_fields("id", "content", "source", "score", "created_at", "category", "domain")
            .dialect(2)
        )
        res_all = r.ft("idx:memories").search(q_all, query_params={"vec": vec_bytes})
        
        # Filtra i duplicati già trovati nel primo passo
        existing_ids = {r["id"] for r in results}
        
        for doc in res_all.docs:
            if doc.id not in existing_ids:
                item = {
                    "id": doc.id,
                    "content": doc.content,
                    "score": float(doc.score),
                    "source": doc.source,
                    "domain": getattr(doc, "domain", "generale")
                }
                results.append(item)
                
        # Mantieni il limite
        results = sorted(results, key=lambda x: x["score"])[:limit]
        logger.debug(f"Domain Gating: allargato all'intero DB, {len(results)} risultati totali")
        return results
        
    except Exception as e:
        logger.error(f"Errore search full DB: {e}")
        return results
