"""
Text-to-Speech con due voci sherpa-onnx:
- Italiano: Piper paola-medium
- Inglese:  Piper lessac-medium
Il cambio voce è istantaneo — zero latenza aggiuntiva.
"""
import numpy as np
from pathlib import Path
from loguru import logger
import sherpa_onnx
import config


def _load_sherpa_model(model_dir: Path) -> sherpa_onnx.OfflineTts:
    import json
    onnx_files = [f for f in model_dir.glob("*.onnx") if not f.name.endswith(".json")]
    if not onnx_files:
        raise FileNotFoundError(f"Modello non trovato in {model_dir}.")
    model_path = onnx_files[0]
    tokens_path = model_dir / "tokens.txt"
    espeak_dir = model_dir / "espeak-ng-data"

    json_path = model_dir / (model_path.name + ".json")
    length_scale, noise_scale, noise_scale_w = 1.0, 0.667, 0.8
    if json_path.exists():
        try:
            inf = json.loads(json_path.read_text()).get("inference", {})
            length_scale = inf.get("length_scale", 1.0)
            noise_scale = inf.get("noise_scale", 0.667)
            noise_scale_w = inf.get("noise_scale_w", 0.8)
        except Exception:
            pass

    cfg = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(model_path),
                lexicon="",
                tokens=str(tokens_path),
                data_dir=str(espeak_dir) if espeak_dir.exists() else "",
                length_scale=length_scale,
                noise_scale=noise_scale,
                noise_scale_w=noise_scale_w,
            ),
            provider="cpu",
            num_threads=4,
            debug=False,
        ),
        rule_fsts="",
        max_num_sentences=1,
    )
    return sherpa_onnx.OfflineTts(cfg)


_LANG_CODES: dict[str, str] = {
    "inglese": "en", "en": "en",
    "francese": "fr", "fr": "fr",
    "spagnolo": "es", "es": "es",
    "tedesco": "de", "de": "de",
    "cinese": "zh", "zh": "zh",
    "italiano": "it", "it": "it",
    "portoghese": "pt", "pt": "pt",
    "russo": "ru", "ru": "ru",
    "giapponese": "ja", "ja": "ja",
    "arabo": "ar", "ar": "ar",
}


def _normalize(samples: np.ndarray) -> np.ndarray:
    peak = np.max(np.abs(samples))
    return samples / peak * 0.95 if peak > 0 else samples


class TTS:
    def __init__(self):
        self._tts_it: sherpa_onnx.OfflineTts = None
        self._tts_en: sherpa_onnx.OfflineTts = None
        self.sample_rate: int = 22050

    def load(self):
        # Voce italiana (sherpa-onnx Paola)
        logger.info("Caricamento voce italiana (Paola)...")
        self._tts_it = _load_sherpa_model(config.TTS_MODEL_DIR)
        self.sample_rate = self._tts_it.sample_rate

        # Voce inglese (sherpa-onnx Lessac) — opzionale
        try:
            logger.info("Caricamento voce inglese (Lessac)...")
            self._tts_en = _load_sherpa_model(config.TTS_MODEL_DIR_EN)
            logger.info(f"Voce inglese pronta — sr: {self._tts_en.sample_rate}Hz")
        except Exception as e:
            logger.warning(f"Voce inglese non caricata: {e} — fallback italiano")

        logger.info(f"TTS pronto — sample rate IT: {self.sample_rate}Hz")

    def synthesize(self, text: str, speed: float = 1.0, lang: str = "it") -> tuple[np.ndarray, int]:
        lang_code = _LANG_CODES.get(lang.lower(), lang.lower()[:2])

        if lang_code == "en" and self._tts_en is not None:
            tts = self._tts_en
        else:
            tts = self._tts_it

        audio = tts.generate(text=text, sid=0, speed=speed)
        samples = np.array(audio.samples, dtype=np.float32)
        sr = tts.sample_rate

        samples = _normalize(samples)
        logger.debug(f"TTS [{lang_code}]: '{text[:50]}' ({len(samples)/sr:.1f}s)")
        return samples, sr
