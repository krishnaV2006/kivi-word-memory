"""Turning a decision into text: protected spans, tokenisation, casing, morphology.

Two rules govern this file:

  1. Never rewrite inside an identifier. An email address, a URL, an @handle or a
     backticked command is not prose. Rewriting aditya@sarvam.ai breaks the address,
     and a broken address is a worse failure than an unpersonalised transcript.

  2. Replace the word, not the user's formatting. An all-caps span stays all-caps; a
     possessive keeps its suffix.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Evaluated in order; all matches are unioned into one set of protected ranges.
PROTECTED_PATTERNS = [
    re.compile(r"`[^`]*`"),                                   # inline code
    re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE),   # urls
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),                  # email addresses
    re.compile(r"(?<!\w)@[\w.-]+"),                           # @handles
    re.compile(r"\b[\w-]+\.(?:com|ai|org|net|io|dev|co|in)\b\S*", re.IGNORECASE),
]

# Devanagari is included so code-mixed text tokenises as words rather than as gaps.
# The phonetic layer transliterates those tokens, so a term taught in one script is
# found in the other.
WORD_RE = re.compile(r"[A-Za-zऀ-ॿ][A-Za-zऀ-ॿ'’-]*")
POSSESSIVE_RE = re.compile(r"(['’]s|['’])$")


@dataclass
class Token:
    text: str        # exactly as it appears, e.g. "Aditya's"
    start: int
    end: int
    stem: str        # matchable core, e.g. "Aditya"
    suffix: str      # e.g. "'s", reattached after replacement


def protected_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for pattern in PROTECTED_PATTERNS:
        for m in pattern.finditer(text or ""):
            ranges.append((m.start(), m.end()))
    return sorted(ranges)


def overlaps_protected(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start < r_end and end > r_start for r_start, r_end in ranges)


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    for m in WORD_RE.finditer(text or ""):
        raw = m.group(0)
        suffix_match = POSSESSIVE_RE.search(raw)
        suffix = suffix_match.group(0) if suffix_match else ""
        stem = raw[: len(raw) - len(suffix)] if suffix else raw
        if not stem:
            continue
        tokens.append(Token(text=raw, start=m.start(), end=m.end(), stem=stem, suffix=suffix))
    return tokens


def render_replacement(source_stem: str, canonical: str, kind: str) -> str:
    """Match the canonical form to the shape of the text it is replacing."""
    if kind == "acronym":
        return canonical                      # IITM is IITM regardless of surroundings
    if len(source_stem) > 1 and source_stem.isupper():
        return canonical.upper()              # the user was shouting; keep shouting
    return canonical                          # canonical already carries its own casing


def apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    """Apply non-overlapping (start, end, replacement) edits right-to-left."""
    out = text
    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        out = out[:start] + replacement + out[end:]
    return out
