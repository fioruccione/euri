"""
Scarica il modello TTS italiano (Piper riccardo-x_low) per sherpa-onnx.
Esegui una volta sola: python scripts/download_tts_model.py
"""
import urllib.request
import zipfile
from pathlib import Path
import sys

MODEL_DIR = Path.home() / "euri" / "models" / "tts" / "it_IT-riccardo-x_low"
BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/it/it_IT/riccardo/x_low"

FILES = {
    "it_IT-riccardo-x_low.onnx": f"{BASE_URL}/it_IT-riccardo-x_low.onnx",
    "tokens.txt": f"{BASE_URL}/it_IT-riccardo-x_low.onnx.json",  # sherpa-onnx usa questo come tokens
    "it_IT-riccardo-x_low.onnx.json": f"{BASE_URL}/it_IT-riccardo-x_low.onnx.json",
}


def download(url: str, dest: Path):
    print(f"  Scarico {dest.name}...", end=" ", flush=True)
    urllib.request.urlretrieve(url, dest)
    size_kb = dest.stat().st_size // 1024
    print(f"OK ({size_kb} KB)")


def main():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Cartella: {MODEL_DIR}\n")

    for filename, url in FILES.items():
        dest = MODEL_DIR / filename
        if dest.exists():
            print(f"  {filename} già presente, skip.")
            continue
        try:
            download(url, dest)
        except Exception as e:
            print(f"ERRORE: {e}")
            print(f"\nSe il download fallisce, scarica manualmente da:")
            print(f"  https://huggingface.co/rhasspy/piper-voices/tree/main/it/it_IT/riccardo/x_low")
            print(f"e metti i file in: {MODEL_DIR}")
            sys.exit(1)

    # Il file tokens.txt per sherpa-onnx Piper è incluso nel .onnx.json
    # Rinomina se necessario
    tokens_path = MODEL_DIR / "tokens.txt"
    json_path = MODEL_DIR / "it_IT-riccardo-x_low.onnx.json"
    if not tokens_path.exists() and json_path.exists():
        import shutil
        shutil.copy(json_path, tokens_path)

    print("\nModello TTS pronto.")


if __name__ == "__main__":
    main()
