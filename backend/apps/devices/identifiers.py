"""Tracker identifier rules per device model (spec A7 step 2)."""
from __future__ import annotations

import re

SERIAL_RE = re.compile(r"^[A-Za-z0-9-]{4,40}$")


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def normalize_identifier(raw: str) -> str:
    return "".join(raw.split())


def identifier_error(identifier: str, identifier_type: str, pattern: str = "") -> str | None:
    """Return a user-facing problem description, or None when valid."""
    if not identifier:
        return "Enter the tracker's identifier."
    if len(identifier) > 40:
        return "Identifiers are at most 40 characters."
    if identifier_type == "imei":
        if not (identifier.isdigit() and len(identifier) == 15):
            return "An IMEI has exactly 15 digits. It's printed on the tracker's label."
        if not luhn_valid(identifier):
            return "This IMEI's check digit is wrong. Check the number on the label for a typo."
        return None
    if identifier_type == "serial":
        return None if SERIAL_RE.match(identifier) else "Use 4-40 letters, digits or dashes."
    try:
        return None if re.fullmatch(pattern, identifier) else "This identifier doesn't match the model's format."
    except re.error:
        return "The device model's identifier pattern is invalid. Ask a platform administrator."
