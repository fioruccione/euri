"""
Tool di scrittura e clipboard per l'Executor sandbox di Euri.

- write_text: salva testo dettato su file + lo copia negli appunti
- clipboard_write: copia testo negli appunti (senza salvare su file)
- clipboard_read: legge il contenuto degli appunti
"""
import re
from datetime import datetime
from pathlib import Path
from agent.executor import ToolResult


# Cartella di output — documenti Euri in Documenti utente
_OUTPUT_DIR = Path.home() / "Documents" / "Euri"


def _sanitize_filename(name: str) -> str:
    """Rimuove caratteri non validi per un nome file."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name).strip(". ")
    return name[:80] or "appunto"


def tool_write_text(params: dict, **kwargs) -> ToolResult:
    """
    Salva testo su file e lo copia negli appunti.
    Parametri:
      - text (str, required): testo da salvare
      - filename (str, optional): nome file senza estensione (default: appunto_YYYYMMDD_HHMM)
      - format (str, optional): 'txt' o 'md' (default: txt)
    """
    text = params.get("text", "").strip()
    if not text:
        return ToolResult(success=False, output="Nessun testo da salvare.", error="empty text")

    fmt = params.get("format", "txt").lower()
    if fmt not in ("txt", "md"):
        fmt = "txt"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    raw_name = params.get("filename", "").strip()
    filename = _sanitize_filename(raw_name) if raw_name else f"appunto_{timestamp}"
    filepath = _OUTPUT_DIR / f"{filename}.{fmt}"

    try:
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # Se il file esiste già, aggiungi timestamp per non sovrascrivere
        if filepath.exists():
            filepath = _OUTPUT_DIR / f"{filename}_{timestamp}.{fmt}"

        filepath.write_text(text, encoding="utf-8")

        # Copia anche negli appunti
        try:
            import pyperclip
            pyperclip.copy(text)
            clipboard_ok = True
        except Exception:
            clipboard_ok = False

        n_words = len(text.split())
        clipboard_msg = " Copiato negli appunti." if clipboard_ok else ""
        output = (
            f"Salvato in {filepath.name}: {n_words} parole.{clipboard_msg} "
            f"Puoi incollarlo dove vuoi con Ctrl+V."
        )
        return ToolResult(
            success=True,
            output=output,
            raw_data={"filepath": str(filepath), "words": n_words, "clipboard": clipboard_ok}
        )

    except Exception as e:
        return ToolResult(success=False, output="Errore nel salvataggio del file.", error=str(e))


def tool_clipboard_write(params: dict, **kwargs) -> ToolResult:
    """
    Copia testo negli appunti senza salvare su file.
    Parametro: text (str, required)
    """
    text = params.get("text", "").strip()
    if not text:
        return ToolResult(success=False, output="Nessun testo da copiare.", error="empty text")

    try:
        import pyperclip
        pyperclip.copy(text)
        n_words = len(text.split())
        return ToolResult(
            success=True,
            output=f"Copiato negli appunti: {n_words} parole. Premi Ctrl+V per incollare.",
            raw_data={"words": n_words}
        )
    except Exception as e:
        return ToolResult(success=False, output="Non riesco ad accedere agli appunti.", error=str(e))


def tool_clipboard_read(params: dict, **kwargs) -> ToolResult:
    """
    Legge il contenuto degli appunti e lo riporta vocalmente (max 200 caratteri).
    """
    try:
        import pyperclip
        text = pyperclip.paste().strip()
        if not text:
            return ToolResult(success=True, output="Gli appunti sono vuoti.")

        preview = text[:200]
        truncated = len(text) > 200
        suffix = f"... e altri {len(text) - 200} caratteri." if truncated else ""
        return ToolResult(
            success=True,
            output=f"Negli appunti c'è: {preview}{suffix}",
            raw_data={"length": len(text), "truncated": truncated}
        )
    except Exception as e:
        return ToolResult(success=False, output="Non riesco a leggere gli appunti.", error=str(e))
