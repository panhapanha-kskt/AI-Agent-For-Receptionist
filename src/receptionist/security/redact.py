"""Mask personal data (emails, phone numbers) before it reaches logs."""

import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# 7+ digits, allowing spaces/dashes/dots and an optional leading +
_PHONE = re.compile(r"\+?\d[\d\s().-]{5,}\d")


def redact(text: str) -> str:
    text = _EMAIL.sub("[email]", text)
    return _PHONE.sub("[phone]", text)
