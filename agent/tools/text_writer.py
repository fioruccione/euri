"""
Tool di scrittura e clipboard per l'Executor sandbox di Euri.

- write_text: salva testo dettato su file + lo copia negli appunti
- clipboard_write: copia testo negli appunti (senza salvare su file)
- clipboard_read: legge il contenuto degli appunti
- clipboard_analyze: analizza il contenuto degli appunti per la sessione corrente
- clipboard_analyze_save: analizza e salva in memoria solo su richiesta esplicita
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
            raw_data={
                "length": len(text),
                "truncated": truncated,
                "context_extra": text[:6000],
                "artifact_content": text,
                "artifact_kind": "clipboard_text",
                "artifact_source": "clipboard",
            }
        )
    except Exception as e:
        return ToolResult(success=False, output="Non riesco a leggere gli appunti.", error=str(e))


_SINGLE_PASS_MAX = 80_000   # chars — sotto questa soglia: analisi diretta
_CHUNK_SIZE      = 20_000   # chars per chunk quando il testo è più lungo
_MAX_CHUNKS      = 4        # max chunk elaborati (= 80K chars totali)


def _analyze_text_full(text: str, cfg, brain) -> str:
    """
    Analizza testo di qualsiasi lunghezza senza troncarlo.
    ≤ 80K chars → singolo passaggio con num_ctx=32768.
    > 80K chars → estrae fatti da ogni chunk (max 4×20K), poi sintesi finale.
    """
    from core.ollama_client import chat_client

    shared_ctx = cfg.CHAT_OLLAMA_NUM_CTX
    _OPTS_SINGLE = {"temperature": 0.3, "num_predict": 1200, "num_ctx": shared_ctx}
    _OPTS_CHUNK  = {"temperature": 0.2, "num_predict": 600,  "num_ctx": shared_ctx}
    _OPTS_SYNTH  = {"temperature": 0.3, "num_predict": 1000, "num_ctx": shared_ctx}

    def _chat(prompt: str, opts: dict) -> str:
        r = chat_client.chat(
            model=cfg.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options=opts,
            think=False,
        )
        return brain._clean(r.message.content or "")

    _NO_MD = "Testo semplice, zero markdown: niente asterischi, niente #, niente backtick."

    if len(text) <= _SINGLE_PASS_MAX:
        prompt = (
            f"Testo ricevuto dagli appunti:\n\n{text}\n\n"
            "Analizza e rispondi in italiano:\n"
            "1. Di cosa si tratta (1-2 frasi)\n"
            "2. I punti tecnici o fattuali più rilevanti (dati, nomi, decisioni, misure)\n"
            "3. Osservazioni o connessioni utili\n"
            f"Denso e diretto. Nessun preambolo. {_NO_MD}"
        )
        return _chat(prompt, _OPTS_SINGLE)

    # testo lungo: chunking
    chunks = [text[i:i + _CHUNK_SIZE] for i in range(0, len(text), _CHUNK_SIZE)][:_MAX_CHUNKS]
    facts = []
    for i, chunk in enumerate(chunks):
        prompt = (
            f"Segmento {i + 1}/{len(chunks)} di un documento lungo.\n\n{chunk}\n\n"
            "Estrai i fatti tecnici e numerici più rilevanti di questo segmento. "
            "Elenco breve, niente frasi introduttive."
        )
        f = _chat(prompt, _OPTS_CHUNK)
        if f:
            facts.append(f"[Parte {i + 1}]\n{f}")

    if not facts:
        return ""

    combined = "\n\n".join(facts)
    synth_prompt = (
        f"Questi sono i punti chiave estratti dalle {len(chunks)} parti di un documento lungo:\n\n"
        f"{combined}\n\n"
        "Scrivi una sintesi coerente in italiano:\n"
        "1. Di cosa tratta il documento complessivo\n"
        "2. I dati e fatti più importanti\n"
        "3. Eventuali conclusioni o connessioni utili\n"
        f"Denso e diretto. Nessun preambolo. {_NO_MD}"
    )
    return _chat(synth_prompt, _OPTS_SYNTH)


def _tool_clipboard_analyze(params: dict, *, persist: bool, **kwargs) -> ToolResult:
    """
    Analizza il contenuto degli appunti (testo lungo o immagine PNG/JPG) con il LLM,
    estrae i fatti rilevanti e, solo con persist=True, li salva in Redis come memoria
    teach. In entrambi i casi l'Executor inietta il risultato nella history della
    sessione, quindi i follow-up possono usarlo senza trasformarlo in conoscenza
    permanente.

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
            if not description.strip():
                return ToolResult(
                    success=False,
                    output="Non sono riuscito ad analizzare l'immagine negli appunti.",
                    error="image analysis failed",
                    raw_data={"persisted": False, "type": "image"},
                )
            mid = None
            if persist:
                mid = memory.save_memory(
                    content=f"Immagine analizzata dagli appunti:\n{description}",
                    category="conoscenza",
                    source="teach",
                    tags=["clipboard", "immagine"],
                    memory_kind="document_summary",
                )
                if not mid:
                    return ToolResult(
                        success=False,
                        output=(
                            "Ho analizzato l'immagine, ma non sono riuscito a "
                            f"salvarla in memoria. {description}"
                        ),
                        error="memory save rejected",
                        raw_data={
                            "memory_id": None,
                            "persisted": False,
                            "type": "image",
                        },
                    )
            action = "analizzato l'immagine e salvato i dettagli in memoria" if persist \
                else "analizzato l'immagine senza salvarla in memoria"
            return ToolResult(
                success=True,
                output=f"Ho {action}. {description}",
                raw_data={
                    "memory_id": mid,
                    "persisted": persist,
                    "type": "image",
                },
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
        summary = _analyze_text_full(text, cfg, brain)
        if not summary:
            return ToolResult(success=False, output="Non sono riuscito ad analizzare il testo.")

        mid = None
        if persist:
            mid = memory.save_memory(
                content=f"Testo analizzato dagli appunti:\n{summary}",
                category="conoscenza",
                source="teach",
                tags=["clipboard", "testo"],
                memory_kind="document_summary",
            )
            if not mid:
                return ToolResult(
                    success=False,
                    output=(
                        f"Ho letto {len(text)} caratteri e prodotto la sintesi, "
                        f"ma non sono riuscito a salvarla in memoria. {summary}"
                    ),
                    error="memory save rejected",
                    raw_data={
                        "memory_id": None,
                        "persisted": False,
                        "type": "text",
                        "original_length": len(text),
                        "context_extra": text[:6000],
                        "artifact_content": text,
                        "artifact_kind": "clipboard_text",
                        "artifact_source": "clipboard",
                    },
                )
        persistence_note = " Ho salvato la sintesi in memoria." if persist \
            else " Non ho salvato nulla in memoria."
        return ToolResult(
            success=True,
            output=f"Ho letto {len(text)} caratteri.{persistence_note} {summary}",
            raw_data={
                "memory_id": mid,
                "persisted": persist,
                "type": "text",
                "original_length": len(text),
                "context_extra": text[:6000],
                "artifact_content": text,
                "artifact_kind": "clipboard_text",
                "artifact_source": "clipboard",
            },
        )
    except Exception as e:
        return ToolResult(success=False, output="Errore nell'analisi del testo.", error=str(e))


def tool_clipboard_analyze(params: dict, **kwargs) -> ToolResult:
    """Analisi temporanea: il risultato vive nella history, non in Redis."""
    return _tool_clipboard_analyze(params, persist=False, **kwargs)


def tool_clipboard_analyze_save(params: dict, **kwargs) -> ToolResult:
    """Analisi persistente, selezionata solo da una richiesta esplicita di salvataggio."""
    return _tool_clipboard_analyze(params, persist=True, **kwargs)


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
                # Alcuni clipboard owner rispondono anche a un target non realmente
                # disponibile. Non passare testo/HTML rinominato .png a Ollama.
                if not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
                    continue
                tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                tmp.write(result.stdout)
                tmp.close()
                return tmp.name
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return None
