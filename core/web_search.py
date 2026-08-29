"""
Ricerca web controllata per Euri.
Usata solo su intent esplicito WEB_SEARCH — mai automatica.
"""
import re
import socket
import requests
from loguru import logger

from core.memory_axes import analyze_memory_axes


def _normalise_anchor(value: str) -> str:
    return " ".join(re.findall(r"\w+", str(value or "").casefold(), re.UNICODE))


def web_results_support_query_entities(
    query: str,
    results: list[dict],
) -> tuple[bool, list[str], list[str]]:
    """Gate di persistenza: almeno un'entita' nominale deve ricomparire.

    Non giudica la verita' delle pagine e non blocca la risposta Web. Impedisce
    soltanto che un risultato manifestamente su un altro soggetto diventi una
    memoria cognitiva associata ai nomi della query.
    """
    anchors = [
        str(item).strip()
        for item in (analyze_memory_axes(query).get("entity_mentions") or [])
        if str(item).strip()
    ]
    if not anchors:
        return True, [], []
    corpus = _normalise_anchor(" ".join(
        str(result.get(field) or "")
        for result in (results or [])
        for field in ("title", "url", "body")
    ))
    supported = [
        anchor for anchor in anchors
        if _normalise_anchor(anchor) in corpus
    ]
    missing = [
        anchor for anchor in anchors
        if _normalise_anchor(anchor) not in corpus
    ]
    return bool(supported), anchors, missing


def is_online(host: str = "8.8.8.8", port: int = 53, timeout: float = 1.5) -> bool:
    """Verifica connettività in <2s. Usa DNS Google come probe."""
    try:
        socket.setdefaulttimeout(timeout)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((host, port))
        return True
    except OSError:
        return False



def fetch_page_text(url: str, max_chars: int = 4000) -> str:
    """
    Scarica una pagina web ed estrae il testo leggibile, eliminando HTML/JS/CSS.
    Ritorna stringa vuota in caso di errore.
    """
    try:
        from bs4 import BeautifulSoup
        headers = {"User-Agent": "Mozilla/5.0 (compatible; Euri/1.0)"}
        resp = requests.get(url, headers=headers, timeout=6)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # Rimuovi script, stili, nav, footer
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        # Normalizza spazi multipli
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]
    except Exception as e:
        logger.debug(f"fetch_page_text {url}: {e}")
        return ""


def search(query: str, max_results: int = 5) -> list[dict]:
    """
    Cerca con DuckDuckGo (ddgs). Ritorna lista di:
      {title, url, body, date}
    Prova prima in italiano, poi in inglese se non trova nulla.
    Scarica il testo completo delle prime 2 pagine utili (esclude social/video).
    """
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            logger.error("Nessun pacchetto di ricerca disponibile (ddgs o duckduckgo_search)")
            return []

    def _run(q: str) -> list[dict]:
        with DDGS() as ddgs:
            raw = list(ddgs.text(q, max_results=max_results))
        return [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "body": r.get("body", ""),
                "date": r.get("published", ""),
            }
            for r in raw
        ]

    # Domini da saltare per il download completo (contenuto non testuale utile)
    _SKIP_DOWNLOAD = (
        "youtube.com", "youtu.be", "threads.com", "instagram.com",
        "facebook.com", "tiktok.com", "twitter.com", "x.com",
        "linkedin.com", "reddit.com",
    )

    try:
        results = _run(query)
        if not results:
            logger.info("Nessun risultato, riprovo in inglese")
            results = _run(query + " english")
        if not results:
            return []

        logger.info(f"Web search '{query}' → {len(results)} risultati")

        # Scarica testo completo delle prime 2 pagine utili
        downloaded = 0
        for r in results:
            if downloaded >= 2:
                break
            url = r.get("url", "")
            if not url or any(d in url for d in _SKIP_DOWNLOAD):
                continue
            page_text = fetch_page_text(url)
            if page_text:
                r["body"] = page_text
                logger.info(f"Pagina scaricata ({downloaded+1}/2): {len(page_text)} chars da {url}")
                downloaded += 1

        # Porta i risultati con contenuto completo in cima
        results.sort(key=lambda r: len(r.get("body", "")), reverse=True)
        return results

    except Exception as e:
        logger.error(f"Errore web search: {e}")
        return []


def answer_explicit_web_search(
    text: str,
    brain,
    memory,
    *,
    semantic_frame: dict | None = None,
    online_check=None,
    search_fn=None,
) -> dict:
    """Esegue una ricerca gia' autorizzata e restituisce un esito per ogni UI.

    Questa funzione non decide l'intento: il chiamante deve aver gia' ricevuto
    ``WEB_SEARCH`` dal frame condiviso. Le dipendenze opzionali servono alle
    regressioni offline e non aprono un secondo classificatore linguistico.
    """
    check = online_check or is_online
    perform_search = search_fn or search
    if not check():
        return {
            "status": "offline",
            "reply": "Non ho internet adesso. Posso rispondere solo da quello che ricordo.",
            "query": "",
            "memory_id": None,
        }

    query = brain.extract_search_query(text, semantic_frame=semantic_frame)
    if not query:
        return {
            "status": "missing_query",
            "reply": "Cosa vuoi che cerchi?",
            "query": "",
            "memory_id": None,
        }

    results = perform_search(query)
    weak_domains = (
        "youtube.com", "youtu.be", "threads.com", "instagram.com",
        "facebook.com", "tiktok.com", "twitter.com", "x.com",
    )
    weak = not results or all(
        any(domain in item.get("url", "") for domain in weak_domains)
        for item in results
    )
    if weak:
        fallback_query = brain.extract_query_fallback(query)
        if fallback_query != query:
            logger.info("Query fallback: '{}' → '{}'", query, fallback_query)
            results = perform_search(fallback_query)
            query = fallback_query

    if not results:
        return {
            "status": "not_found",
            "reply": f"Non ho trovato niente di utile su '{query}'.",
            "query": query,
            "memory_id": None,
        }

    summary = brain.summarize_web_results(results, query)
    persistence_allowed, anchors, missing = web_results_support_query_entities(
        query, results
    )
    memory_id = None
    if persistence_allowed:
        memory_id = memory.save_memory(
            content=f"Ricerca web '{query}':\n{summary}",
            category="web",
            tags=["web_search"],
            source="web",
            final_fields={"requires_verification": True},
        )
    else:
        logger.warning(
            "Web search non persistita: risultati senza entita' query anchors={} missing={}",
            anchors,
            missing,
        )
    if memory_id:
        logger.info(
            "Web search salvata in memoria: {}… (query: '{}')",
            str(memory_id)[:8],
            query[:50],
        )
    elif persistence_allowed:
        logger.warning(
            "Web search NON salvata: contenuto sospetto bloccato dal MemoryGuard "
            "(query '{}')",
            query[:50],
        )
    return {
        "status": "ok",
        "reply": summary,
        "query": query,
        "memory_id": memory_id,
        "persistence": "saved" if memory_id else (
            "skipped_entity_mismatch" if not persistence_allowed else "save_rejected"
        ),
        "persistence_missing_entities": missing,
    }
