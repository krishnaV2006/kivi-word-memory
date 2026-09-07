"""Verify that every number claimed in the documentation matches generated results.

Run: python -m eval.check_docs

This repository makes a lot of numeric claims — pass rates, false-positive counts,
latency, database sizes — spread across README.md, RUN.md and DISCOVERIES.md. Every one
of them was true when written. The failure mode is that a later change quietly makes one
false, and a reviewer who finds a stale number in the README has no reason to trust the
numbers they cannot check.

So the claims are checked against `eval/results/*.json` mechanically. Anything this
reports is either a doc that needs updating or a result that moved unexpectedly; both are
worth knowing before submitting.

Exit code is 1 if any claim is stale, so this can gate a commit.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS = REPO_ROOT / "eval" / "results"


def load(name: str) -> dict | None:
    path = RESULTS / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    results = load("results.json")
    adversarial = load("adversarial.json")
    scale = load("scale.json")

    if results is None:
        print("eval/results/results.json is missing — run `python -m eval.run_eval` first")
        return 1

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    runmd = (REPO_ROOT / "RUN.md").read_text(encoding="utf-8")

    ph = results["aggregate"]["phonetic"]
    ed = results["aggregate"]["exact_dict"]
    total = ph["total"]

    # (description, text that must appear, document, document name)
    checks: list[tuple[str, str, str, str]] = [
        ("RUN.md quotes the actual pass line",
         f"{ph['passed']}/{total} passed", runmd, "RUN.md"),
        ("RUN.md quotes the actual intervention line",
         f"useful {ph['useful_intervention']}  false {ph['false_intervention']}  "
         f"missed {ph['missed_intervention']}  wrong {ph['wrong_intervention']}",
         runmd, "RUN.md"),
        ("README case count",
         f"Measured over {total} cases", readme, "README.md"),
        ("README useful/false headline",
         f"**{ph['useful_intervention']} useful interventions, "
         f"{ph['false_intervention']} false interventions**", readme, "README.md"),
        ("README ablation row: cases passed",
         f"| cases passed | {results['aggregate']['none']['passed']} / {total} "
         f"| {ed['passed']} / {total} | **{ph['passed']} / {total}** |",
         readme, "README.md"),
        ("README ablation row: useful",
         f"| useful interventions | 0 | {ed['useful_intervention']} "
         f"| **{ph['useful_intervention']}** |", readme, "README.md"),
        ("README ablation row: false",
         f"| false interventions | 0 | **{ed['false_intervention']}** "
         f"| **{ph['false_intervention']}** |", readme, "README.md"),
        ("README dataset composition total",
         f"{total} cases:", readme, "README.md"),
    ]

    if adversarial is not None:
        n = adversarial["total_sentences"]
        checks += [
            ("README adversarial headline",
             f"**{n:,} sentences containing no memory term. "
             f"{adversarial['total_interventions']} interventions.**", readme, "README.md"),
            ("README adversarial inline claim",
             f"**0 false interventions across {n:,} sentences**", readme, "README.md"),
            ("RUN.md adversarial claim",
             f"({n} sentences, {adversarial['total_interventions']} interventions)",
             runmd, "RUN.md"),
        ]
        for corpus in ("neutral", "names", "homophone"):
            row = adversarial["by_corpus"].get(corpus)
            if row:
                checks.append((
                    f"README adversarial row: {corpus}",
                    f"| {corpus} | {row['sentences']} | **{row['interventions']}** |",
                    readme, "README.md"))

    failures: list[str] = []
    for description, needle, document, doc_name in checks:
        if needle not in document:
            failures.append(f"{doc_name}: {description}\n      expected to find: {needle!r}")

    # Latency and scale claims are prose rather than exact strings, so these are sanity
    # bounds rather than string matches -- enough to catch an order-of-magnitude drift.
    lat = results.get("latency", {})
    if lat.get("cold", {}).get("p50_ms", 0) > 20:
        failures.append(
            f"cold p50 is {lat['cold']['p50_ms']} ms, but README claims single-digit "
            f"milliseconds — one of them is wrong")
    if scale is not None:
        last = scale["points"][-1]
        if last["warm"]["p50_ms"] > 10:
            failures.append(
                f"warm p50 at {last['total_entries']} entries is "
                f"{last['warm']['p50_ms']} ms; README claims resolution is flat in the "
                f"size of memory")
        # The README states the 10k cold figure in prose. Tie it to the measurement, with
        # a wide tolerance because this one genuinely varies run to run -- I have now got
        # this number wrong twice by quoting a stale run rather than re-measuring.
        claimed = re.search(r"cold p50 reaches roughly (\d+) ms at 10,000 entries", readme)
        if claimed is None:
            failures.append("README no longer states a 10k cold p50 figure; "
                            "check_docs cannot verify it")
        else:
            want, got = int(claimed.group(1)), last["cold"]["p50_ms"]
            if not (0.6 * got <= want <= 1.4 * got):
                failures.append(
                    f"README claims cold p50 of ~{want} ms at 10,000 entries; "
                    f"scale.json measured {got} ms")
        if not last["correct"]:
            failures.append(
                f"scale assertions fail at {last['total_entries']} entries, but the "
                f"scale report claims they hold at every size")

    print(f"checked {len(checks)} documented claims against generated results")
    if failures:
        print(f"\n{len(failures)} STALE CLAIM(S):\n")
        for f in failures:
            print(f"  - {f}\n")
        return 1
    print("every documented number matches the generated results")
    return 0


if __name__ == "__main__":
    sys.exit(main())
