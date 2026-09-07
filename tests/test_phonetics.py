"""Unit tests for the phonetic layer. Run: python -m tests.test_phonetics

Plain asserts, no pytest -- one fewer dependency for the reviewing agent to install.
"""
from __future__ import annotations

import sys

from app.phonetics import (
    consonant_skeleton,
    indic_skeleton,
    keys_for,
    metaphone_key,
    normalize,
    similarity,
)

FAILURES: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual != expected:
        FAILURES.append(f"{label}: expected {expected!r}, got {actual!r}")


def check_true(label: str, cond: bool) -> None:
    if not cond:
        FAILURES.append(label)


def test_variants_collapse() -> None:
    """The product claim: one correction generalises to variants never observed."""
    variants = ["aditya", "aaditya", "adithya", "aadithya", "adhitya", "Aaditya", "AADITYA"]
    keys = {indic_skeleton(v) for v in variants}
    check("aditya variants collapse to one key", len(keys), 1)


def test_homophone_collapses() -> None:
    """kivi and kiwi genuinely sound identical. The guard belongs in the resolver,
    not here -- phonetics must not pretend a real homophone is distinguishable."""
    check("kiwi == kivi phonetically", indic_skeleton("kiwi"), indic_skeleton("kivi"))


def test_romanisation_drift_needs_loose_key() -> None:
    """Interior vowel drift defeats the strict key; the loose key catches it."""
    check_true(
        "vekariya/vakaria differ on strict key",
        indic_skeleton("vekariya") != indic_skeleton("vakaria"),
    )
    check(
        "vekariya/vakaria share loose key",
        consonant_skeleton("vekariya"),
        consonant_skeleton("vakaria"),
    )


def test_distinct_names_stay_distinct() -> None:
    check_true("aaditya != aditi", indic_skeleton("aaditya") != indic_skeleton("aditi"))
    check_true("kivi != cave", similarity("kivi", "cave") < 0.8)


def test_metaphone_is_insufficient_alone() -> None:
    """Documents why we do not rely on Metaphone: it splits the headline case."""
    check_true(
        "metaphone fails to unify aaditya/adithya",
        metaphone_key("Aaditya") != metaphone_key("adithya"),
    )


def test_multiword_indexes_as_one_identity() -> None:
    pairs = keys_for("Sarvam Kivi")
    algos = {a for _, a in pairs}
    check("three key algos emitted", algos, {"indic", "indic_loose", "metaphone"})
    strict = next(k for k, a in pairs if a == "indic")
    check("multiword strict key is joined", strict, "sarvamkivi")


def test_normalize_strips_punctuation() -> None:
    check("possessive stripped", normalize("Aaditya's"), "aadityas")
    check("skeleton of possessive", indic_skeleton("Aaditya's"), "aditias")
    check("empty stays empty", normalize("!!!"), "")


def test_similarity_bounds() -> None:
    check_true("identical scores 1.0", similarity("kivi", "kivi") >= 0.999)
    check("empty scores 0.0", similarity("", "kivi"), 0.0)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} check(s):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"ok - {len(tests)} phonetics tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
