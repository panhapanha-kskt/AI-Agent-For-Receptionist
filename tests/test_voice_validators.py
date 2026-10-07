import io

import pytest

from receptionist.voice.validators import AudioRejected, sniff_format, validate_audio

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 200
WAV = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 200


@pytest.mark.parametrize(
    ("head", "fmt"),
    [
        (WEBM, "webm"),
        (b"OggS" + b"\x00" * 20, "ogg"),
        (WAV, "wav"),
        (b"ID3" + b"\x00" * 20, "mp3"),
        (b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 8, "mp4"),
        (b"MZ\x90\x00 an exe file", None),
        (b"<script>alert(1)</script>", None),
    ],
)
def test_sniff_format(head, fmt):
    assert sniff_format(head[:16]) == fmt


def test_valid_webm_accepted():
    data, fmt = validate_audio(io.BytesIO(WEBM), "audio/webm;codecs=opus", 1024)
    assert fmt == "webm" and data == WEBM


def test_wrong_content_type_rejected():
    with pytest.raises(AudioRejected):
        validate_audio(io.BytesIO(WEBM), "application/x-msdownload", 1024)


def test_fake_audio_rejected():
    # Says it is audio, but the bytes are not.
    with pytest.raises(AudioRejected):
        validate_audio(io.BytesIO(b"MZ" + b"\x00" * 500), "audio/webm", 1024)


def test_oversized_rejected_without_full_read():
    class Endless(io.RawIOBase):
        reads = 0

        def read(self, n=-1):
            Endless.reads += 1
            return b"\x00" * 65536

    with pytest.raises(AudioRejected, match="too large"):
        validate_audio(Endless(), "audio/webm", 10 * 65536)
    assert Endless.reads <= 11


def test_too_short_rejected():
    with pytest.raises(AudioRejected):
        validate_audio(io.BytesIO(b"OggS"), "audio/ogg", 1024)
