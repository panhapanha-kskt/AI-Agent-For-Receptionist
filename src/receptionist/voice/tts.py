"""Text-to-speech behind one small interface, so providers can be swapped.

- "browser": the server returns no audio; the web page speaks the reply with the
  browser's speechSynthesis. Zero cost; Khmer voice depends on the device.
- "mms": Meta MMS-TTS (facebook/mms-tts-eng, facebook/mms-tts-khm), self-hosted.
  LICENCE: CC-BY-NC 4.0, non-commercial use only. For commercial use, add a provider
  for a licensed service (e.g. Google Cloud TTS, which has a km-KH voice).
"""

import io
import threading
from typing import Protocol

from receptionist.config import Settings


class TTS(Protocol):
    def synthesize(self, text: str, lang: str) -> bytes | None:
        """Return WAV bytes, or None if the client should speak the text itself."""


class BrowserTTS:
    def synthesize(self, text: str, lang: str) -> bytes | None:
        return None


class MmsTTS:
    MODELS = {"en": "facebook/mms-tts-eng", "km": "facebook/mms-tts-khm"}

    def __init__(self, revision: str):
        # Pin a commit hash in production so a changed upstream model can't slip in.
        self.revision = revision
        self._loaded: dict[str, tuple[object, object]] = {}
        self._lock = threading.Lock()

    def _load(self, lang: str):
        with self._lock:
            if lang not in self._loaded:
                from transformers import AutoTokenizer, VitsModel

                name = self.MODELS[lang]
                self._loaded[lang] = (
                    VitsModel.from_pretrained(name, revision=self.revision),
                    AutoTokenizer.from_pretrained(name, revision=self.revision),
                )
            return self._loaded[lang]

    def synthesize(self, text: str, lang: str) -> bytes | None:
        import numpy as np
        import scipy.io.wavfile
        import torch

        model, tokenizer = self._load(lang if lang in self.MODELS else "en")
        inputs = tokenizer(text, return_tensors="pt")
        with torch.no_grad():
            waveform = model(**inputs).waveform[0].numpy()
        buf = io.BytesIO()
        pcm = (np.clip(waveform, -1, 1) * 32767).astype(np.int16)
        scipy.io.wavfile.write(buf, model.config.sampling_rate, pcm)
        return buf.getvalue()


def make_tts(settings: Settings) -> TTS:
    if settings.tts_provider == "mms":
        return MmsTTS(settings.tts_mms_revision)
    return BrowserTTS()
