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


# --- Devanagari -------------------------------------------------------------------
# Kivi is built for Indian users, so the same person's name arrives in two scripts. A
# term taught once in Latin should be recognised in Devanagari without being taught
# again -- the memory is the sound, and the script is just how it was written down.
#
# This is transliteration into the skeleton alphabet, not a rendering scheme: it only has
# to be consistent enough that आदित्य and "Aaditya" land on the same key. Consonants carry
# an inherent 'a' unless a vowel sign or virama follows, which is the only real subtlety.

_DEVA_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "ळ": "l",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    "क़": "k", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f",
}
_DEVA_VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ii", "उ": "u", "ऊ": "uu",
    "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऋ": "ri", "ॲ": "a", "ऑ": "o",
}
_DEVA_MATRAS = {
    "ा": "aa", "ि": "i", "ी": "ii", "ु": "u", "ू": "uu",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ृ": "ri", "ॉ": "o", "ॅ": "a",
}
_DEVA_SIGNS = {"ं": "n", "ँ": "n", "ः": "h"}
_VIRAMA = "्"

DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")


def transliterate_devanagari(word: str) -> str:
    """Devanagari -> Latin, aimed at the phonetic skeleton rather than at readability.

    The one rule that matters here is **schwa deletion**: Hindi drops the word-final
    inherent vowel, so सर्वम is "sarvam" and not "sarvama". It is not unconditional
    though -- the vowel survives after a conjunct, which is why आदित्य is "aaditya" and
    not "aadity". Getting this wrong breaks the match against the Latin spelling in
    exactly the cases the feature exists for.
    """
    out: list[str] = []
    # (index into out, was the consonant part of a conjunct) for inherent-vowel emissions
    last_inherent: tuple[int, bool] | None = None
    i, n = 0, len(word)
    prev_was_virama = False

    while i < n:
        ch = word[i]
        nxt = word[i + 1] if i + 1 < n else ""
        if ch in _DEVA_CONSONANTS:
            base = _DEVA_CONSONANTS[ch]
            if nxt == _VIRAMA:            # explicit vowel suppression
                out.append(base)
                last_inherent = None
                prev_was_virama = True
                i += 2
                continue
            if nxt in _DEVA_MATRAS:       # vowel sign replaces the inherent vowel
                out.append(base + _DEVA_MATRAS[nxt])
                last_inherent = None
                prev_was_virama = False
                i += 2
                continue
            out.append(base + "a")        # inherent vowel
            last_inherent = (len(out) - 1, prev_was_virama)
            prev_was_virama = False
            i += 1
            continue
        if ch in _DEVA_VOWELS:
            out.append(_DEVA_VOWELS[ch])
            last_inherent = None
        elif ch in _DEVA_SIGNS:
            out.append(_DEVA_SIGNS[ch])
            last_inherent = None
        prev_was_virama = False
        i += 1

    # Word-final schwa deletion, unless the consonant closed a conjunct.
    if last_inherent is not None:
        idx, in_conjunct = last_inherent
        if idx == len(out) - 1 and not in_conjunct:
            out[idx] = out[idx][:-1]
    return "".join(out)


def normalize(word: str) -> str:
    """Lowercase and strip everything that is not a letter.

    Devanagari is transliterated first, so a term learned in one script is found in the
    other. Everything downstream -- skeletons, keys, context terms -- is unchanged.
    """
    if DEVANAGARI_RE.search(word):
        word = transliterate_devanagari(word)
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
            if ch == "c":
                # Soft c. Before e, i or y an English c is an /s/ -- service, city, nice,
                # price -- and mapping every bare c to k turned 'service' into 'servike'.
                # That was wrong on its own terms, and it also kept the English context
                # term 'service' from ever meeting Hindi 'सर्विस' (sarvis), which is the
                # same borrowed word. 'ch' never reaches here; it is consumed above.
                # Tuple, not a string: `"" in "eiy"` is True in Python, which silently
                # softened every word-final c and turned 'music' into 'musis'.
                nxt = word[i + 1] if i + 1 < n else ""
                out.append("s" if nxt in ("e", "i", "y") else "k")
            else:
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


# Consonants that Indian ASR genuinely confuses, beyond what the skeleton already folds.
# Deliberately tiny. Every pair added here widens the net for every entry at once, and
# the cost of a wrong rewrite is higher than the cost of a miss.
_CONFUSABLE = {"v": "b", "w": "b", "b": "b"}


def fuzzy_skeleton(word: str) -> str:
    """Deliberately over-permissive key, folding b/v/w together.

    A Gujarati or Bengali speaker may say either, and ASR returns 'Bekariya' for
    'Vekariya'. We refuse to fold b and v in the ordinary skeleton, because that would
    also collapse bat/vat, bet/vet and ban/van -- ordinary English words that must never
    be confused. So the fold lives in its own retrieval tier that the resolver only
    trusts when the sentence supplies independent context. See DISCOVERIES.md section 7.

    vekariya / vakaria / bekariya -> bkr
    """
    skel = consonant_skeleton(word)
    if not skel:
        return ""
    return "".join(_CONFUSABLE.get(c, c) for c in skel)


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
    fuzzy = "".join(fuzzy_skeleton(t) for t in tokens)
    meta = "".join(metaphone_key(t) for t in tokens)
    pairs = []
    if strict:
        pairs.append((strict, "indic"))
    if loose:
        pairs.append((loose, "indic_loose"))
    if fuzzy:
        pairs.append((fuzzy, "indic_fuzzy"))
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
