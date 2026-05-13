"""
Tool di scrittura e clipboard per l'Executor sandbox di Euri.

- write_text: salva testo dettato su file + lo copia negli appunti
- clipboard_write: copia testo negli appunti (senza salvare su file)
- clipboard_read: legge il contenuto degli appunti
- clipboard_analyze: analizza il contenuto degli appunti (testo o immagine) e lo salva in memoria
"""
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from agent.executor import ToolResult


def _read_clipboard_text() -> str:
    """
    Legge il testo dalla clipboard con fallback multi-backend.
    Ordine: wl-paste (Wayland) → xclip (X11) → pyperclip.
    Necessario perché su Wayland pyperclip legge la clipboard X11 (XWayland)
    mentre le app native scrivono sulla clipboard Wayland.
    """
    # 1. Wayland nativo (wl-clipboard)
    if os.environ.get("WAYLAND_DISPLAY"):
        try:
            result = subprocess.run(
                ["wl-paste", "--no-newline"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                return result.stdout
        except FileNotFoundError:
            pass  # wl-paste non installato — prosegui con fallback
        except Exception:
            pass

    # 2. X11 / XWayland
    try:
        result = subprocess.run(
            ["xclip", "-o", "-selection", "clipboard"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass

    # 3. pyperclip (ultimo tentativo)
    try:
        import pyperclip
        return pyperclip.paste()
    except Exception:
        return ""


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
        text = _read_clipboard_text().strip()
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


def tool_clipboard_analyze(params: dict, **kwargs) -> ToolResult:
    """
    Analizza il contenuto degli appunti (testo lungo o immagine PNG/JPG) con il LLM,
    estrae i fatti rilevanti e li salva in Redis come memoria teach.

    kwargs attesi: brain (Brain), memory (MemoryManager)
    """
    brain   = kwargs.get("brain")
    memory  = kwargs.get("memory")
    if not brain or not memory:
        return ToolResult(success=False, output="Componenti interni non disponibili.", error="missing brain/memory")

    # ── Branch immagine: cerca PNG nella clipboard X11 ──────────────────────
    img_path = _clipboard_image()
    if img_path:
        try:
            description = brain.analyze_image(img_path, question=(
                "Descrivi questa immagine in dettaglio in italiano. "
                "Se contiene dati tecnici, tabelle o specifiche, riportali fedelmente. "
                "Usa frasi complete, niente elenchi puntati."
            ))
            mid = memory.save_memory(
                content=f"Immagine analizzata dagli appunti:\n{description}",
                category="conoscenza",
                source="teach",
                tags=["clipboard", "immagine"],
            )
            return ToolResult(
                success=True,
                output=f"Ho analizzato l'immagine e salvato i dettagli in memoria. {description[:200]}",
                raw_data={"memory_id": mid, "type": "image"},
            )
        except Exception as e:
            return ToolResult(success=False, output="Errore nell'analisi dell'immagine.", error=str(e))
        finally:
            try:
                Path(img_path).unlink(missing_ok=True)
            except Exception:
                pass

    # ── Branch testo ─────────────────────────────────────────────────────────
    try:
        text = _read_clipboard_text().strip()
    except Exception as e:
        return ToolResult(success=False, output="Non riesco ad accedere agli appunti.", error=str(e))

    if not text:
        return ToolResult(success=True, output="Gli appunti sono vuoti, niente da analizzare.")

    try:
        import ollama, config as cfg
        prompt = (
            f"Hai ricevuto questo testo dagli appunti dell'utente:\n\n{text[:6000]}\n\n"
            "Sintetizza i punti chiave in 3-6 frasi concise in italiano. "
            "Estrai fatti concreti: nomi, date, dati tecnici, decisioni. "
            "Niente commenti sul testo stesso, solo i contenuti."
        )
        response = ollama.chat(
            model=cfg.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.2, "num_predict": 400},
            think=False,
        )
        summary = brain._clean(response.message.content or "")
        if not summary:
            return ToolResult(success=False, output="Non sono riuscito a sintetizzare il testo.")

        mid = memory.save_memory(
            content=f"Testo analizzato dagli appunti:\n{summary}",
            category="conoscenza",
            source="teach",
            tags=["clipboard", "testo"],
        )
        char_info = f"{len(text)} caratteri" + (" (troncato a 6000)" if len(text) > 6000 else "")
        return ToolResult(
            success=True,
            output=f"Ho letto {char_info} e salvato i punti chiave. {summary[:180]}",
            raw_data={"memory_id": mid, "type": "text", "original_length": len(text)},
        )
    except Exception as e:
        return ToolResult(success=False, output="Errore nell'analisi del testo.", error=str(e))


def _clipboard_image() -> str | None:
    """
    Prova a estrarre un'immagine PNG dalla clipboard.
    Fallback: wl-paste (Wayland) → xclip (X11).
    Ritorna il path del file temporaneo, o None se la clipboard non contiene un'immagine.
    """
    backends = []
    if os.environ.get("WAYLAND_DISPLAY"):
        backends.append(["wl-paste", "--no-newline", "--type", "image/png"])
    backends.append(["xclip", "-o", "-selection", "clipboard", "-t", "image/png"])

    for cmd in backends:
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=3)
            if result.returncode == 0 and result.stdout:
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tmp.write(result.stdout)
                tmp.close()
                return tmp.name
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return None
