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
import re


# Pulisce il markdown dal testo PRIMA della sintesi vocale: il TTS leggeva gli asterischi
# ("asterisco asterisco") e gli altri marcatori. Tocca SOLO l'audio — il testo
# loggato/mostrato in Silent Chat/salvato resta col suo markdown.
_MD_BULLET = re.compile(r'(?m)^[ \t]*[-*•]\s+')
_MD_HEADER = re.compile(r'(?m)^[ \t]*#{1,6}[ \t]*')
_SPEECH_BOUNDARY = re.compile(r"(?<=[.!?…])\s+|\n+")


def _strip_markup_for_speech(text: str) -> str:
    if not text:
        return text
    t = _MD_BULLET.sub("", text)            # bullet a inizio riga (*, -, •)
    t = _MD_HEADER.sub("", t)               # heading markdown (#)
    t = t.replace("*", "").replace("`", "")  # asterischi e backtick residui (grassetto/corsivo/code)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def split_for_speech(text: str, *, max_chars: int = 360) -> list[str]:
    """Divide il testo finale in segmenti TTS senza cambiarne il contenuto.

    La divisione e' puramente di trasporto audio: avviene dopo che la risposta
    completa e' stata generata e validata. Preferisce i confini di frase; una
    frase eccezionalmente lunga viene spezzata sull'ultimo spazio disponibile.
    """
    cleaned = _strip_markup_for_speech(text)
    if not cleaned:
        return []
    max_chars = max(40, int(max_chars))

    def _wrap(unit: str) -> list[str]:
        wrapped: list[str] = []
        remaining = unit.strip()
        while len(remaining) > max_chars:
            cut = remaining.rfind(" ", 0, max_chars + 1)
            if cut < max_chars // 2:
                cut = max_chars
            wrapped.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if remaining:
            wrapped.append(remaining)
        return wrapped

    units: list[str] = []
    for sentence in _SPEECH_BOUNDARY.split(cleaned):
        if sentence.strip():
            units.extend(_wrap(sentence))

    chunks: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current} {unit}".strip() if current else unit
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = unit
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


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
        text = _strip_markup_for_speech(text)  # niente asterischi/markdown letti ad alta voce
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
