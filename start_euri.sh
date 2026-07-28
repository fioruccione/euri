#!/bin/bash
# Script per avviare Euri con i path corretti per le librerie CUDA 12

set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

export LD_LIBRARY_PATH="$PWD/venv/lib/python3.12/site-packages/nvidia/cublas/lib:$PWD/venv/lib/python3.12/site-packages/nvidia/cudnn/lib:${LD_LIBRARY_PATH:-}"
# Questa installazione è il banco di prova live della policy selettiva. Il
# chiamante può sempre tornare a `on`, `shadow` o `off` esportando esplicitamente
# la variabile prima dell'avvio.
export EURI_RAG_DUAL_CHANNEL_MODE="${EURI_RAG_DUAL_CHANNEL_MODE:-selective}"

UI_PID=""
cleanup() {
    status=$?
    trap - EXIT INT TERM
    if [[ -n "$UI_PID" ]] && kill -0 "$UI_PID" 2>/dev/null; then
        kill "$UI_PID" 2>/dev/null || true
        for _ in {1..20}; do
            kill -0 "$UI_PID" 2>/dev/null || break
            sleep 0.1
        done
        if kill -0 "$UI_PID" 2>/dev/null; then
            kill -KILL "$UI_PID" 2>/dev/null || true
        fi
        wait "$UI_PID" 2>/dev/null || true
    fi
    exit "$status"
}
trap cleanup EXIT INT TERM

echo "=> Avvio di Euri con LD_LIBRARY_PATH configurato per CUDA 12"

echo "=> Avvio Streamlit Control Room in background..."
mkdir -p logs
./venv/bin/streamlit run ui/app.py \
    --server.fileWatcherType=none \
    --server.port=8501 \
    > logs/control_room.log 2>&1 &
UI_PID=$!
sleep 1
if ! kill -0 "$UI_PID" 2>/dev/null; then
    echo "=> ERRORE: Control Room non avviata. Controlla logs/control_room.log (porta 8501 occupata?)." >&2
    exit 1
fi

echo "=> Avvio Interocezione Hardware in background..."
# Il recettore ha un lock proprio ed e' intenzionalmente indipendente dal ciclo
# di vita della UI/Voice Daemon, cosi' la baseline continua tra i riavvii. Una
# sessione separata evita che il Ctrl+C diretto al foreground arresti anche lui.
setsid --fork ./venv/bin/python hardware_monitor.py \
    </dev/null >> logs/hardware_monitor.log 2>&1

echo "=> Avvio Voice Daemon..."
./venv/bin/python voice_daemon.py
