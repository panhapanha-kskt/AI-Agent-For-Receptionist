"""Input/output checks around the model."""

import re
import unicodedata

_KHMER = re.compile(r"[ក-៿]")
MAX_REPLY_CHARS = 3000


class InputRejected(ValueError):
    pass


def clean_input(text: str, max_chars: int) -> str:
    """Normalize caller text and enforce the length limit."""
    text = unicodedata.normalize("NFC", text)
    # Drop control characters (keep newlines and tabs).
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    text = text.strip()
    if not text:
        raise InputRejected("Message is empty.")
    if len(text) > max_chars:
        raise InputRejected(f"Message is too long (max {max_chars} characters).")
    return text


def clean_output(text: str) -> str:
    return text.strip()[:MAX_REPLY_CHARS]


def detect_lang(text: str) -> str:
    """'km' if the text is mostly Khmer script, else 'en'."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return "en"
    khmer = sum(1 for ch in letters if _KHMER.match(ch))
    return "km" if khmer / len(letters) > 0.3 else "en"
