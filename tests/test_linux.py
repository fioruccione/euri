import numpy as np
from loguru import logger
from voice.stt import STT
from voice.audio_io import play_audio, _sd_disabled

def main():
    logger.info("Test caricamento STT (faster-whisper) su CUDA...")
    try:
        stt = STT()
        stt.load()
        logger.info("STT caricato con successo!")
    except Exception as e:
        logger.error(f"Errore STT: {e}")

    logger.info("Test Audio I/O...")
    logger.info(f"Stato sounddevice bypassato (_sd_disabled): {_sd_disabled}")
    # Genera un secondo di onda sinusoidale a 440 Hz
    sample_rate = 16000
    t = np.linspace(0, 1, sample_rate, False)
    audio_data = np.sin(440 * t * 2 * np.pi).astype(np.float32)
    try:
        play_audio(audio_data, sample_rate)
        logger.info("Riproduzione audio terminata (dovresti aver sentito un beep di 1 secondo).")
    except Exception as e:
        logger.error(f"Errore Audio I/O: {e}")

if __name__ == "__main__":
    main()
