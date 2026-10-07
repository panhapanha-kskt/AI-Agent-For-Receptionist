"""Validate uploaded audio before any decoder touches it.

Checks: declared content type, size limit (enforced while reading, so a huge upload
is never fully buffered), and the file's real format from its magic bytes.
"""

from typing import BinaryIO

ALLOWED_CONTENT_TYPES = {
    "audio/webm",
    "video/webm",  # some browsers label MediaRecorder output this way
    "audio/ogg",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mpeg",
    "audio/mp3",
    "audio/mp4",
    "audio/x-m4a",
    "audio/aac",
}
CHUNK = 64 * 1024


class AudioRejected(ValueError):
    pass


def sniff_format(head: bytes) -> str | None:
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if head.startswith(b"OggS"):
        return "ogg"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mp3"
    if head[4:8] == b"ftyp":
        return "mp4"
    return None


def read_limited(stream: BinaryIO, max_bytes: int) -> bytes:
    chunks, total = [], 0
    while chunk := stream.read(CHUNK):
        total += len(chunk)
        if total > max_bytes:
            raise AudioRejected(f"Audio file is too large (max {max_bytes // (1024 * 1024)} MB).")
        chunks.append(chunk)
    return b"".join(chunks)


def validate_audio(stream: BinaryIO, content_type: str | None, max_bytes: int) -> tuple[bytes, str]:
    base_type = (content_type or "").split(";")[0].strip().lower()
    if base_type not in ALLOWED_CONTENT_TYPES:
        raise AudioRejected("Unsupported audio type.")
    data = read_limited(stream, max_bytes)
    if len(data) < 64:
        raise AudioRejected("Audio file is empty or too short.")
    fmt = sniff_format(data[:16])
    if fmt is None:
        raise AudioRejected("File content is not a supported audio format.")
    return data, fmt
