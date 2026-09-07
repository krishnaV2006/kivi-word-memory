"""Scale measurement. Run: python -m eval.scale

The README used to assert that this design "would not be fine at 10,000 entries". That
was a guess. This measures it.

The question is not really "is SQLite fast" — it is whether the *retrieval* design
degrades. Three keys per surface and a deliberately promiscuous consonant key mean the
candidate set can grow with the size of memory, and if it does, both latency and
precision get worse together: more candidates means more chances for one to win a span it
should not have.

So this reports two things at each size, because either one alone is misleading:

  latency    p50/p95 for a resolution, cold and warm
  precision  candidates retrieved per span, and whether the seeded assertions still hold

A system that stays fast by retrieving nothing is not fast, it is broken, so the
correctness check runs at every size.
"""
from __future__ import annotations

import json
import os
import random
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "eval" / "results"
SCALE_DB = REPO_ROOT / "eval" / ".scale.db"

os.environ.setdefault("KIVI_DB_PATH", str(SCALE_DB))

from app import db as db_mod                                    # noqa: E402
from app.memory import observe                                  # noqa: E402
from app.resolver import _retrieve, load_view, resolve          # noqa: E402
from seed.seed import seed as run_seed                          # noqa: E402

SEED = 20260907
SIZES = (10, 100, 1000, 5000, 10000)

# Syllable pool for generating plausible Indian names, so the synthetic entries sit in
# the same phonetic space as the real ones. Filling memory with random ASCII would make
# the numbers look good by making every key miss.
_ONSET = ["k", "kh", "g", "ch", "j", "t", "th", "d", "dh", "n", "p", "b", "bh", "m",
          "y", "r", "l", "v", "sh", "s", "h", "pr", "kr", "sr", "shr", "tr"]
_NUCLEUS = ["a", "aa", "i", "ee", "u", "oo", "e", "ai", "o", "au"]
_CODA = ["", "n", "m", "r", "l", "s", "t", "k", "sh", "nd", "nt"]


def synth_name(rng: random.Random) -> str:
    syllables = rng.randint(2, 3)
    parts = []
    for _ in range(syllables):
        parts.append(rng.choice(_ONSET) + rng.choice(_NUCLEUS) + rng.choice(_CODA))
    return "".join(parts).capitalize()


def _drop_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        p = SCALE_DB.with_name(SCALE_DB.name + suffix)
        if p.exists():
            p.unlink()


# The seeded assertions that must keep holding as memory grows. If precision decays with
# scale, these are where it shows up first.
CORRECTNESS = [
    ("adithya is joining the call", "Adithya is joining the call.",
     "Aaditya is joining the call."),
    ("ask aditya to review the sarvam kiwi service",
     "Ask Aditya to review the Sarvam Kiwi service.",
     "Ask Aaditya to review the Sarvam Kivi service."),
    ("i ate a kiwi for breakfast", "I ate a kiwi for breakfast.",
     "I ate a kiwi for breakfast."),
    ("ask rahul to review the pull request", "Ask Rahul to review the pull request.",
     "Ask Rahul to review the pull request."),
]

PROBE = ("ask aditya to review the sarvam kiwi service",
         "Ask Aditya to review the Sarvam Kiwi service.")


def measure(conn, view, repeats: int) -> dict:
    resolve(conn, *PROBE, view=view, record=False)
    xs = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        resolve(conn, *PROBE, view=view, record=False)
        xs.append((time.perf_counter() - t0) * 1000)
    xs.sort()
    return {
        "p50_ms": round(xs[len(xs) // 2], 3),
        "p95_ms": round(xs[int(len(xs) * 0.95)], 3),
        "mean_ms": round(statistics.mean(xs), 3),
    }


def run() -> dict:
    rng = random.Random(SEED)
    points = []

    for size in SIZES:
        _drop_db()
        db_mod.migrate(verbose=False)
        conn = db_mod.connect()
        run_seed(conn, verbose=False)

        # Fill memory with synthetic pinned entries. dictionary_add makes them active
        # immediately, which is the worst case for retrieval: every one can compete.
        for _ in range(size):
            observe(conn, {"kind": "dictionary_add", "canonical": synth_name(rng),
                           "entry_kind": "person"})

        view = load_view(conn)
        n_entries = conn.execute("SELECT COUNT(*) AS c FROM entries").fetchone()["c"]

        # How many candidates does a single span pull back at this size?
        spans = ["Aditya", "Kiwi", "Rahul", "Vakaria", "Bekariya"]
        retrieved = [len(_retrieve(view, s)) for s in spans]

        repeats = 25 if size >= 5000 else 40
        cold = measure(conn, None, repeats)
        warm = measure(conn, view, repeats)

        failures = []
        for asr, fmt, expected in CORRECTNESS:
            got = resolve(conn, asr, fmt, view=view, record=False).memory_aware
            if got != expected:
                failures.append({"input": fmt, "expected": expected, "actual": got})

        stats = db_mod.db_stats(conn)
        conn.close()

        points.append({
            "synthetic_entries": size,
            "total_entries": n_entries,
            "phonetic_keys": stats["rows"].get("phonetic_keys", 0),
            "total_rows": stats["total_rows"],
            "db_kb": round(stats["bytes"] / 1024, 1),
            "candidates_per_span": {
                "max": max(retrieved), "mean": round(statistics.mean(retrieved), 2),
            },
            "cold": cold,
            "warm": warm,
            "correctness_failures": failures,
            "correct": not failures,
        })
        print(f"  {size:>6} entries  cold p50 {cold['p50_ms']:>7.3f} ms  "
              f"warm p50 {warm['p50_ms']:>6.3f} ms  "
              f"max candidates {max(retrieved):>3}  "
              f"{'ok' if not failures else 'CORRECTNESS FAILURE'}")

    _drop_db()
    return {"seed": SEED, "sizes": list(SIZES), "points": points}


def write_report(payload: dict) -> None:
    pts = payload["points"]
    lines: list[str] = []
    w = lines.append
    w("# Scale\n")
    w("Generated by `python -m eval.scale` (deterministic, seed "
      f"`{payload['seed']}`). Memory is filled with synthetic Indian-style names built "
      "from a syllable pool, so the added entries sit in the same phonetic space as the "
      "real ones — filling memory with random ASCII would flatter the numbers by making "
      "every key miss. All synthetic entries are added via `dictionary_add`, so they are "
      "active immediately and every one of them is allowed to compete.\n")
    w("| entries | phonetic keys | db | cold p50 | cold p95 | warm p50 | max candidates/span | assertions hold |")
    w("|---:|---:|---:|---:|---:|---:|---:|:-:|")
    for p in pts:
        w(f"| {p['total_entries']} | {p['phonetic_keys']} | {p['db_kb']} KB "
          f"| {p['cold']['p50_ms']} ms | {p['cold']['p95_ms']} ms | {p['warm']['p50_ms']} ms "
          f"| {p['candidates_per_span']['max']} | {'yes' if p['correct'] else '**NO**'} |")

    first, last = pts[0], pts[-1]
    size_growth = round(last["total_entries"] / first["total_entries"], 1)
    cold_growth = round(last["cold"]["p50_ms"] / first["cold"]["p50_ms"], 1) if first["cold"]["p50_ms"] else 0
    warm_growth = round(last["warm"]["p50_ms"] / first["warm"]["p50_ms"], 1) if first["warm"]["p50_ms"] else 0

    w("\n## Reading it\n")
    w(f"Memory grows **{size_growth}x** across this table. Cold p50 grows **{cold_growth}x**; "
      f"warm p50 grows **{warm_growth}x**. That gap is the whole result, and it separates two "
      "things the earlier single-size latency number could not.\n")
    w(f"**Resolution is effectively flat in the size of memory.** Warm p50 goes from "
      f"{first['warm']['p50_ms']} ms at {first['total_entries']} entries to "
      f"{last['warm']['p50_ms']} ms at {last['total_entries']}. Retrieval is an indexed key "
      "lookup, so what a span pulls back depends on how many entries share its *sound*, not "
      "on how many entries exist — which is why the `max candidates/span` column barely "
      "moves.\n")
    w(f"**Loading memory is linear, and that is the cost of the no-cache decision.** Cold "
      f"p50 reaches {last['cold']['p50_ms']} ms at {last['total_entries']} entries because "
      "the HTTP layer reads all of memory on every request, deliberately, so that an "
      "observation recorded a moment ago cannot be missed. At seed scale that costs a few "
      f"milliseconds; at {last['total_entries']} entries it costs about "
      f"{round(last['cold']['p50_ms'] - last['warm']['p50_ms'])} ms. The fix is a cache "
      "invalidated on write, and the number above is what it would buy — measured rather "
      "than guessed.\n")
    w("The `max candidates/span` column is the one that matters for correctness. If it "
      "climbed with memory size, precision would decay at scale — more candidates means "
      "more chances for one to win a span it should not have — and the assertions column "
      "would start failing. A system that stayed fast by retrieving nothing would show up "
      "there too.\n")

    failing = [p for p in pts if not p["correct"]]
    if failing:
        w("\n## Correctness failures\n")
        for p in failing:
            w(f"\n**At {p['total_entries']} entries:**\n")
            for f in p["correctness_failures"]:
                w(f"- input `{f['input']}`\n  - expected `{f['expected']}`\n"
                  f"  - actual `{f['actual']}`")
    else:
        w("\nEvery seeded assertion — the headline rewrite, the two-identity sentence, the "
          "fruit that must stay a fruit, and the unknown name that must stay untouched — "
          "holds at every size measured.\n")

    (RESULTS_DIR / "scale.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"measuring at {SIZES} entries ...")
    payload = run()
    (RESULTS_DIR / "scale.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_report(payload)
    print("\nwritten to eval/results/scale.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
