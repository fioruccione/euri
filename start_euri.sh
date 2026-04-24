#!/bin/bash
# Script per avviare Euri con i path corretti per le librerie CUDA 12

export LD_LIBRARY_PATH=$PWD/venv/lib/python3.12/site-packages/nvidia/cublas/lib:$PWD/venv/lib/python3.12/site-packages/nvidia/cudnn/lib:$LD_LIBRARY_PATH

echo "=> Avvio di Euri con LD_LIBRARY_PATH configurato per CUDA 12"

echo "=> Avvio Streamlit Control Room in background..."
./venv/bin/streamlit run ui/app.py --server.fileWatcherType=none > /dev/null 2>&1 &

echo "=> Avvio Voice Daemon..."
./venv/bin/python voice_daemon.py
