"""Phonetic keys for romanised Indian names and product terms.

Why not just Metaphone: Metaphone is English-centric. It maps `th` to a theta sound as in
"think", but in Indian romanisation `th` is an aspirated `t` -- "Adithya" is "Aditya" with a
breathier consonant, not a lisp. Measured on the brief's own example:

    metaphone("Aaditya") -> "TTY"
    metaphone("adithya") -> "AT0Y"      # does not match

The headline case of this task fails on Metaphone alone. So we compute our own skeleton using
the systematic rewrite rules that actually govern how Indian names get romanised, and keep
Metaphone only as a supplementary recall net.

Three keys per surface, serving different jobs:

  indic        strict skeleton, vowels preserved   -- high precision
  indic_loose  consonant skeleton, vowels dropped  -- high recall
  metaphone    English phonetic algorithm          -- supplementary recall

Keys are for *candidate retrieval*. They deliberately over-generate; the resolver's scoring,
evidence and context guards make the actual decision. A key collision is not an intervention.
"""
from __future__ import annotations

import re

import jellyfish

VOWELS = set("aeiou")

# Tried longest-first at each position. The right-hand side is the emitted sound, not a
# placeholder -- 'th' and 't' both emit 't', which is exactly the collapse we want.
_DIGRAPHS: list[tuple[str, str]] = [
    ("chh", "c"),
    ("aa", "a"), ("ee", "i"), ("ii", "i"), ("oo", "u"), ("uu", "u"),
    ("ch", "c"), ("sh", "s"), ("zh", "s"),
    ("th", "t"), ("dh", "d"), ("bh", "b"), ("gh", "g"), ("jh", "j"),
    ("kh", "k"), ("ph", "f"), ("ck", "k"), ("wh", "v"),
]

_SINGLES: dict[str, str] = {
    "w": "v",   # Vekariya / Wekariya
    "z": "j",   # Zubin / Jubin
    "q": "k",
    "c": "k",   # bare c (ch already consumed above)
    "x": "ks",
}


def normalize(word: str) -> str:
    """Lowercase and strip everything that is not a letter."""
    return re.sub(r"[^a-z]", "", word.lower())


def _emit(word: str) -> str:
    """Single left-to-right scan applying digraph then single-letter rules."""
    out: list[str] = []
    i = 0
    n = len(word)
    while i < n:
        for src, dst in _DIGRAPHS:
            if word.startswith(src, i):
                out.append(dst)
                i += len(src)
                break
        else:
            ch = word[i]
            out.append(_SINGLES.get(ch, ch))
            i += 1
    return "".join(out)


def _collapse_repeats(s: str) -> str:
    """vikkas -> vikas, aaditya -> aditya (after digraph pass)."""
    out: list[str] = []
    for ch in s:
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def indic_skeleton(word: str) -> str:
    """Strict, vowel-preserving phonetic skeleton.

    aditya / aaditya / adithya / aadithya / adhitya  ->  aditia
    kivi / kiwi                                      ->  kivi
    """
    w = normalize(word)
    if not w:
        return ""
    w = _emit(w)
    # 'ya'/'iya' both realise the same glide: aditya and aditiya are one name.
    w = re.sub(r"iya", "ia", w)
    w = re.sub(r"ya", "ia", w)
    w = _collapse_repeats(w)
    return w


def consonant_skeleton(word: str) -> str:
    """Loose key: initial letter plus consonants. Absorbs interior vowel drift.

    vekariya / vakaria / vekaria -> vkr
    """
    skel = indic_skeleton(word)
    if not skel:
        return ""
    head, tail = skel[0], skel[1:]
    return head + "".join(c for c in tail if c not in VOWELS)


def metaphone_key(word: str) -> str:
    w = normalize(word)
    return jellyfish.metaphone(w) if w else ""


def keys_for(surface: str) -> list[tuple[str, str]]:
    """All (key, algo) pairs for a surface. Multiword phrases are joined, so
    'Sarvam Kivi' indexes as one identity rather than two loose tokens."""
    tokens = [t for t in re.split(r"\s+", surface.strip()) if t]
    if not tokens:
        return []
    strict = "".join(indic_skeleton(t) for t in tokens)
    loose = "".join(consonant_skeleton(t) for t in tokens)
    meta = "".join(metaphone_key(t) for t in tokens)
    pairs = []
    if strict:
        pairs.append((strict, "indic"))
    if loose:
        pairs.append((loose, "indic_loose"))
    if meta:
        pairs.append((meta, "metaphone"))
    return pairs


def similarity(a: str, b: str) -> float:
    """How alike do these two surfaces sound? 0..1.

    Blends skeleton agreement with raw-string agreement so that two surfaces which collapse
    to the same skeleton but look wildly different still score below an exact restatement.
    """
    sa, sb = indic_skeleton(a), indic_skeleton(b)
    if not sa or not sb:
        return 0.0
    skel = jellyfish.jaro_winkler_similarity(sa, sb)
    raw = jellyfish.jaro_winkler_similarity(normalize(a), normalize(b))
    return round(0.75 * skel + 0.25 * raw, 4)
