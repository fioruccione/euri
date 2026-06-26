"""
draft_writer — salva una BOZZA in una cartella di REVISIONE visibile, separata
dagli appunti generici di text_writer. La bozza NON viene mai inviata: è solo un
artefatto da rivedere ("fammi una bozza ma non inviarla", "salvami il risultato
dove posso rivederlo"). Riusa il pattern sanitize+mkdir di text_writer.
"""
import re
from datetime import datetime
from pathlib import Path

import config


def _review_dir() -> Path:
    return Path(getattr(
        config, "WORKFLOW_REVIEW_DIR",
        str(Path.home() / "Documents" / "Euri" / "Revisione"),
    ))


def _sanitize(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\n\r\t]', '_', name).strip(". ")
    return name[:60] or "bozza"


def save_review(text: str, title: str = "") -> str:
    """
    Scrive `text` come .md nella cartella di revisione e ritorna il path assoluto.
    Non sovrascrive: se il nome esiste, aggiunge un timestamp.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("niente da salvare per la revisione")

    d = _review_dir()
    d.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    base = _sanitize(title) if title else f"bozza_{ts}"
    path = d / f"{base}.md"
    if path.exists():
        path = d / f"{base}_{ts}.md"

    path.write_text(text, encoding="utf-8")
    return str(path)
