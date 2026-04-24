"""
Ricerca web controllata per Euri.
Usata solo su intent esplicito WEB_SEARCH — mai automatica.
"""
import re
import socket
import requests
from loguru import logger


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


def search(query: str, max_results: int = 3) -> list[dict]:
    """
    Cerca con DuckDuckGo (ddgs). Ritorna lista di:
      {title, url, body, date}
    Prova prima in italiano, poi in inglese se non trova nulla.
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

    try:
        results = _run(query)
        if not results:
            logger.info("Nessun risultato in italiano, riprovo in inglese")
            results = _run(query + " english")
        if not results:
            return []

        logger.info(f"Web search '{query}' → {len(results)} risultati")

        # Scegli la pagina più pertinente da scaricare:
        # evita Wikipedia e pagine generiche che raramente contengono dati freschi
        _SKIP_DOMAINS = ("wikipedia.org", "wikimedia.org", "wikidata.org")
        best_idx = 0
        for i, r in enumerate(results):
            url = r.get("url", "")
            if not any(d in url for d in _SKIP_DOMAINS):
                best_idx = i
                break

        best_url = results[best_idx].get("url", "")
        if best_url:
            page_text = fetch_page_text(best_url)
            if page_text:
                results[best_idx]["body"] = page_text
                logger.info(f"Contenuto pagina scaricato: {len(page_text)} caratteri da {best_url}")

        return results
    except Exception as e:
        logger.error(f"Errore web search: {e}")
        return []
