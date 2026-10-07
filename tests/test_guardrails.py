import pytest

from receptionist.agent.guardrails import InputRejected, clean_input, detect_lang
from receptionist.security.redact import redact


def test_clean_input_strips_control_chars():
    assert clean_input("  hi\x00\x07 there \n", 100) == "hi there"


@pytest.mark.parametrize("text", ["", "   ", "x" * 101])
def test_clean_input_rejects(text):
    with pytest.raises(InputRejected):
        clean_input(text, 100)


@pytest.mark.parametrize(
    ("text", "lang"),
    [
        ("What are the school fees?", "en"),
        ("តើថ្លៃសិក្សាប៉ុន្មាន?", "km"),
        ("តើ grade 7 ថ្លៃសិក្សាប៉ុន្មាន?", "km"),
        ("12345", "en"),
    ],
)
def test_detect_lang(text, lang):
    assert detect_lang(text) == lang


def test_redact_pii():
    out = redact("Call me on +855 12 345 678 or mail sok@example.com")
    assert "345" not in out and "example.com" not in out
    assert "[phone]" in out and "[email]" in out
