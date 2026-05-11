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
