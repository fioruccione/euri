#!/bin/bash
# ─────────────────────────────────────────
#  DEBUG EURI — avvio interattivo in terminale
#  Ferma il demone launchd, poi avvia voice_daemon
#  direttamente con output visibile.
#  Al Ctrl+C chiede se riavviare il demone.
# ─────────────────────────────────────────

BREW_BIN="/opt/homebrew/bin"
export PATH="$BREW_BIN:/usr/local/bin:/usr/bin:/bin:$PATH"
OLLAMA_BIN="/usr/local/bin/ollama"
PLIST="$HOME/Library/LaunchAgents/com.euri.daemon.plist"

cd "$(dirname "$0")"

echo "=== EURI DEBUG MODE ==="
echo ""

# Ferma il demone launchd se attivo
if launchctl list | grep -q "com.euri.daemon"; then
    echo "Fermo il demone launchd..."
    launchctl unload "$PLIST"
    sleep 1
fi

# Killa eventuali processi voice_daemon rimasti
EXISTING=$(pgrep -f "voice_daemon.py")
if [ -n "$EXISTING" ]; then
    echo "Termino processo esistente (PID $EXISTING)..."
    kill "$EXISTING"
    sleep 1
fi

# Assicura Redis attivo
if ! "$BREW_BIN/redis-cli" ping &>/dev/null; then
    echo "[1/3] Avvio Redis..."
    "$BREW_BIN/redis-stack-server" --loglevel warning &
    sleep 2
else
    echo "[1/3] Redis già attivo ✓"
fi

# Assicura Ollama attivo
if ! "$OLLAMA_BIN" list &>/dev/null; then
    echo "[2/3] Avvio Ollama..."
    "$OLLAMA_BIN" serve &
    sleep 3
else
    echo "[2/3] Ollama già attivo ✓"
fi

echo ""
echo "─────────────────────────────────────────"
echo "  Euri in ascolto (modalità debug)."
echo "  Ctrl+C per fermare."
echo "─────────────────────────────────────────"
echo ""

# Trap Ctrl+C per gestire uscita pulita
cleanup() {
    echo ""
    echo "─────────────────────────────────────────"
    echo "Euri fermato."
    echo ""
    read -r -p "Riattivare il demone automatico? [s/N] " risposta
    if [[ "$risposta" =~ ^[sS]$ ]]; then
        launchctl load "$PLIST"
        echo "Demone riavviato. Euri torna in background."
    else
        echo "Demone disattivato. Per riattivarlo:"
        echo "  launchctl load $PLIST"
    fi
    exit 0
}
trap cleanup INT TERM

# Avvia in foreground con output diretto
# Filtra i warning objc del dynamic linker (conflitto libavdevice PyAV/OpenCV — cosmético)
venv/bin/python3 voice_daemon.py 2> >(grep -v "^objc\[" >&2)

# Se voice_daemon esce da solo (non da Ctrl+C)
echo ""
echo "voice_daemon.py è uscito."
read -r -p "Riattivare il demone automatico? [s/N] " risposta
if [[ "$risposta" =~ ^[sS]$ ]]; then
    launchctl load "$PLIST"
    echo "Demone riavviato."
fi
