"""Speech-to-text with faster-whisper (self-hosted; audio never leaves the server).

English (and language detection) uses STT_MODEL_EN, a multilingual Whisper model.
Khmer uses STT_MODEL_KM when set: a Khmer fine-tuned Whisper converted to CTranslate2,
because stock Whisper is weak at Khmer. See README for conversion steps.
"""

import io
import threading
from dataclasses import dataclass

from receptionist.config import Settings


class STTUnavailable(RuntimeError):
    pass


class AudioTooLong(ValueError):
    pass


@dataclass
class Transcript:
    text: str
    lang: str
    duration: float


class WhisperSTT:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._models: dict[str, object] = {}
        self._lock = threading.Lock()

    def _model(self, name: str):
        with self._lock:
            if name not in self._models:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise STTUnavailable(
                        "faster-whisper is not installed: pip install -e .[voice]"
                    ) from exc
                self._models[name] = WhisperModel(
                    name,
                    device=self.settings.stt_device,
                    compute_type=self.settings.stt_compute_type,
                )
            return self._models[name]

    def warm_up(self) -> None:
        """Load the speech models now, so the first voice message isn't slowed by loading
        (and, the very first time, downloading) them."""
        if not self.settings.stt_enabled:
            return
        for name in filter(None, [self.settings.stt_model_en, self.settings.stt_model_km]):
            self._model(name)

    def _run(self, model_name: str, audio: bytes, language: str | None):
        model = self._model(model_name)
        # transcribe() decodes the audio and detects the language up front; the
        # segments generator does the (expensive) decoding work lazily.
        segments, info = model.transcribe(
            io.BytesIO(audio), language=language, beam_size=5, vad_filter=True
        )
        if info.duration > self.settings.max_audio_seconds:
            raise AudioTooLong(
                f"Voice message is too long (max {self.settings.max_audio_seconds} seconds)."
            )
        return segments, info

    def transcribe(self, audio: bytes, lang_hint: str | None = None) -> Transcript:
        if not self.settings.stt_enabled:
            raise STTUnavailable("Voice input is disabled (set STT_ENABLED=true).")
        km_model = self.settings.stt_model_km

        if lang_hint == "km" and km_model:
            segments, info = self._run(km_model, audio, "km")
            lang = "km"
        else:
            segments, info = self._run(self.settings.stt_model_en, audio, lang_hint)
            lang = "km" if info.language == "km" else "en"
            if lang == "km" and km_model:
                segments, info = self._run(km_model, audio, "km")

        text = " ".join(s.text.strip() for s in segments).strip()
        return Transcript(text=text, lang=lang, duration=info.duration)
