"""
Tool lettura log per l'Executor sandbox di Euri.
Solo path pre-approvati in whitelist — nessun path libero dall'utente.
"""
from pathlib import Path
from agent.executor import ToolResult
import config

LOG_WHITELIST: dict[str, Path] = {
    "euri":         config.BASE_DIR / "logs" / "voice_daemon.log",
    "voice_daemon": config.BASE_DIR / "logs" / "voice_daemon.log",
    "executor":     config.BASE_DIR / "logs" / "executor_audit.log",
}


def tool_read_log(params: dict) -> ToolResult:
    log_key = params.get("log_name", "euri")
    n_lines = params.get("n_lines", 20)

    log_path = LOG_WHITELIST.get(log_key)
    if not log_path:
        return ToolResult(success=False, output=f"Log '{log_key}' non riconosciuto.", error="unknown_log")

    if not log_path.exists():
        return ToolResult(success=False, output=f"Il file di log non esiste ancora.", error="file_not_found")

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = lines[-n_lines:]
        if not tail:
            return ToolResult(success=True, output="Il log è vuoto.", raw_data={})

        # Conta errori e warning
        errors = sum(1 for l in tail if "ERROR" in l)
        warnings = sum(1 for l in tail if "WARNING" in l)

        summary = f"Ultime {len(tail)} righe del log Euri."
        if not errors and not warnings:
            summary += " Nessun errore o warning."
        else:
            if errors:
                summary += f" Trovati {errors} errori."
            if warnings:
                summary += f" Trovati {warnings} warning."
            # Includi un riassunto voice-friendly delle righe rilevanti
            relevant = [
                l for l in tail
                if "ERROR" in l or "WARNING" in l or "afplay" in l
                or "AudioQueue" in l or "sounddevice" in l or "timeout" in l
            ]
            if relevant:
                clean = []
                for l in relevant[-5:]:  # max 5 righe
                    # "2026-04-12 17:52:00 | ERROR | module:func:line | messaggio" → "messaggio"
                    parts = l.split(" | ", 3)
                    msg = parts[-1].strip() if len(parts) >= 2 else l.strip()
                    # Rimuovi path file temporanei e percorsi Python
                    import re as _re
                    msg = _re.sub(r"Command \[.*?\]", "comando audio", msg)
                    msg = _re.sub(r"voice\.\w+:\w+:\d+\s*-\s*", "", msg)
                    msg = _re.sub(r"__main__:\w+:\d+\s*-\s*", "", msg)
                    msg = msg[:120]  # max 120 chars per riga
                    if msg:
                        clean.append(msg)
                if clean:
                    summary += " Dettaglio: " + ". ".join(clean)

        return ToolResult(
            success=True,
            output=summary,
            raw_data={"lines": tail, "errors": errors, "warnings": warnings}
        )
    except Exception as e:
        return ToolResult(success=False, output="Errore nella lettura del log.", error=str(e))
