"""The evaluation. Run: python -m eval.run_eval

Method, stated up front so the numbers can be argued with:

  * Every case starts from a database created from scratch: migrate, then seed (unless
    the case sets skip_seed), then apply the case's own observations. No case can be
    contaminated by a previous one.
  * The dataset was authored and committed BEFORE the resolver existed. See the repo
    history -- the commit adding eval/cases/ precedes the commit adding app/resolver.py.
  * Useful interventions and false interventions are counted separately, because a
    system that rewrites everything scores well on recall alone.
  * Doing nothing to text that was already correct is not an intervention and is not
    counted as one in either direction.
  * Cases marked known_hard are expected to fail. They stay in the set and are reported
    as failures. Removing them would make the numbers better and the evaluation worse.

Outputs land in eval/results/: summary.md, results.json, and one JSON file per case
under eval/results/cases/ carrying inputs, expectation, actual, memory state and the
full decision trace.
"""
from __future__ import annotations

import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = REPO_ROOT / "eval" / "cases"
RESULTS_DIR = REPO_ROOT / "eval" / "results"
CASE_RESULTS_DIR = RESULTS_DIR / "cases"
EVAL_DB = REPO_ROOT / "eval" / ".eval.db"

# Point every import at a scratch database so the reviewer's demo data is never touched.
os.environ["KIVI_DB_PATH"] = str(EVAL_DB)

from app import db as db_mod            # noqa: E402
from app.memory import memory_state, observe  # noqa: E402
from app.resolver import resolve        # noqa: E402
from eval import baselines              # noqa: E402
from seed.seed import seed as run_seed  # noqa: E402


# ------------------------------------------------------------------ case preparation

def load_cases() -> list[dict]:
    cases: list[dict] = []
    for path in sorted(CASES_DIR.glob("*.json")):
        cases.extend(json.loads(path.read_text(encoding="utf-8")))
    return cases


def fresh_db(case: dict):
    """A clean database in the state this case describes."""
    if EVAL_DB.exists():
        EVAL_DB.unlink()
    db_mod.migrate(verbose=False)
    conn = db_mod.connect()
    if not case.get("skip_seed"):
        run_seed(conn, verbose=False)
    else:
        from seed.seed import load_common_words
        load_common_words(conn)      # the guard list is not persona data
    for obs in case.get("observations", []):
        observe(conn, obs)
    return conn


# ---------------------------------------------------------------------- classification

def classify(case: dict, actual: str) -> str:
    """One label per case per strategy."""
    expected = case["expected"]
    formatted = case["formatted"] or case["asr"]
    if case["expect_intervention"]:
        if actual == expected:
            return "useful_intervention"
        if actual == formatted:
            return "missed_intervention"
        return "wrong_intervention"
    if actual == expected:
        return "correct_abstention"
    return "false_intervention"


GOOD = {"useful_intervention", "correct_abstention"}


# --------------------------------------------------------------------------- measuring

def latency_profile(repeats: int = 30) -> dict:
    """Warm-path timing on a fixed sentence, measured after a discarded warm-up run.

    Reported honestly: this is a per-request cold read of the whole memory from SQLite
    with no caching layer, which is the dominant cost. See README limitations.
    """
    case = {"skip_seed": False, "observations": []}
    conn = fresh_db(case)
    asr = "ask aditya to review the sarvam kiwi service"
    fmt = "Ask Aditya to review the Sarvam Kiwi service."
    resolve(conn, asr, fmt)                       # warm-up, discarded
    samples, stages = [], []
    for _ in range(repeats):
        t0 = time.perf_counter()
        r = resolve(conn, asr, fmt)
        samples.append((time.perf_counter() - t0) * 1000)
        stages.append(r.timings_ms)
    conn.close()
    samples.sort()
    pct = lambda p: round(samples[min(len(samples) - 1, int(len(samples) * p))], 3)  # noqa: E731
    stage_means = {
        k: round(statistics.mean(s[k] for s in stages), 3) for k in stages[0]
    }
    return {
        "repeats": repeats,
        "warm": True,
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "max_ms": round(max(samples), 3),
        "mean_ms": round(statistics.mean(samples), 3),
        "stage_means_ms": stage_means,
    }


def db_growth_curve(points=(10, 50, 200)) -> list[dict]:
    """How storage grows with ordinary use. Observations are recycled from the seed."""
    seed_obs = json.loads((REPO_ROOT / "seed" / "seed.json").read_text(encoding="utf-8"))["observations"]
    curve = []
    for n in points:
        if EVAL_DB.exists():
            EVAL_DB.unlink()
        db_mod.migrate(verbose=False)
        conn = db_mod.connect()
        from seed.seed import load_common_words
        load_common_words(conn)
        for i in range(n):
            observe(conn, dict(seed_obs[i % len(seed_obs)]))
        stats = db_mod.db_stats(conn)
        entries = conn.execute("SELECT COUNT(*) AS c FROM entries").fetchone()["c"]
        conn.close()
        curve.append({
            "observations": n,
            "entries": entries,
            "total_rows": stats["total_rows"],
            "bytes": stats["bytes"],
            "kb": round(stats["bytes"] / 1024, 1),
        })
    return curve


# ------------------------------------------------------------------------------ runner

def run_case(case: dict) -> dict:
    per_strategy: dict[str, dict] = {}
    trace: list[dict] = []
    mem_snapshot: list[dict] = []
    status_check: dict | None = None

    for strategy in baselines.STRATEGIES:
        conn = fresh_db(case)
        try:
            actual, intervened = baselines.run(strategy, conn, case["asr"], case["formatted"])
            outcome = classify(case, actual)
            per_strategy[strategy] = {
                "actual": actual,
                "intervened": intervened,
                "outcome": outcome,
                "passed": outcome in GOOD,
            }
            if strategy == "phonetic":
                result = resolve(conn, case["asr"], case["formatted"])
                trace = [
                    {k: v for k, v in d.__dict__.items() if k != "candidates"} | {
                        "candidates": d.candidates[:3]
                    }
                    for d in result.decisions
                    if d.action != "no_candidate"
                ]
                mem_snapshot = [
                    {
                        "canonical": e["canonical"], "status": e["status"],
                        "confidence": e["confidence"], "protected": e["protected"],
                        "evidence": e["evidence"],
                        "surfaces": [s["surface"] for s in e["surfaces"]],
                        "context_terms": [c["term"] for c in e["context_terms"]][:8],
                    }
                    for e in memory_state(conn)
                ]
                if case.get("expect_status"):
                    by_name = {e["canonical"]: e["status"] for e in memory_state(conn)}
                    status_check = {
                        "expected": case["expect_status"],
                        "actual": {k: by_name.get(k) for k in case["expect_status"]},
                    }
                    status_check["passed"] = all(
                        by_name.get(k) == v for k, v in case["expect_status"].items()
                    )
        finally:
            conn.close()

    record = {
        "id": case["id"],
        "category": case["category"],
        "description": case["description"],
        "rationale": case["rationale"],
        "known_hard": bool(case.get("known_hard")),
        "inputs": {
            "asr": case["asr"],
            "formatted": case["formatted"],
            "seeded": not case.get("skip_seed"),
            "case_observations": case.get("observations", []),
        },
        "expected": case["expected"],
        "expect_intervention": case["expect_intervention"],
        "strategies": per_strategy,
        "passed": per_strategy["phonetic"]["passed"],
        "outcome": per_strategy["phonetic"]["outcome"],
        "status_check": status_check,
        "decision_trace": trace,
        "memory_state": mem_snapshot,
    }
    return record


def aggregate(records: list[dict]) -> dict:
    agg: dict[str, dict] = {}
    for strategy in baselines.STRATEGIES:
        counts = {
            "useful_intervention": 0, "missed_intervention": 0, "wrong_intervention": 0,
            "correct_abstention": 0, "false_intervention": 0,
        }
        for r in records:
            counts[r["strategies"][strategy]["outcome"]] += 1
        useful = counts["useful_intervention"]
        bad_fires = counts["wrong_intervention"] + counts["false_intervention"]
        missed = counts["missed_intervention"]
        precision = useful / (useful + bad_fires) if (useful + bad_fires) else 0.0
        recall = useful / (useful + missed + counts["wrong_intervention"]) if (
            useful + missed + counts["wrong_intervention"]) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        passed = sum(1 for r in records if r["strategies"][strategy]["passed"])
        agg[strategy] = {
            **counts,
            "passed": passed,
            "total": len(records),
            "pass_rate": round(passed / len(records), 4) if records else 0.0,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
    return agg


# ------------------------------------------------------------------------------ report

def write_summary(payload: dict) -> None:
    a = payload["aggregate"]
    recs = payload["cases"]
    lines: list[str] = []
    w = lines.append

    w("# Evaluation results\n")
    w(f"Generated by `python -m eval.run_eval` on {payload['generated_at']}.\n")
    w(f"**{a['phonetic']['passed']} of {a['phonetic']['total']} cases pass.** "
      f"{payload['known_hard_count']} cases are marked `known_hard` and are expected to "
      f"fail; they are kept in the set deliberately.\n")

    w("\n## What the abstraction bought\n")
    w("Same 44 cases, three strategies. `exact_dict` is whole-word replacement of every "
      "observed surface -- what most people mean by \"a dictionary\".\n")
    w("| metric | none | exact_dict | phonetic |")
    w("|---|---:|---:|---:|")
    rows = [
        ("cases passed", "passed"), ("pass rate", "pass_rate"),
        ("useful interventions", "useful_intervention"),
        ("missed interventions", "missed_intervention"),
        ("wrong target", "wrong_intervention"),
        ("false interventions", "false_intervention"),
        ("correct abstentions", "correct_abstention"),
        ("precision", "precision"), ("recall", "recall"), ("f1", "f1"),
    ]
    for label, key in rows:
        w(f"| {label} | {a['none'][key]} | {a['exact_dict'][key]} | {a['phonetic'][key]} |")

    w("\n### Reading the table\n")
    w("- `none` never intervenes, so it scores every should-not-fire case correctly and "
      "every should-fire case wrong. It is the floor.\n"
      "- `exact_dict` handles spellings it was explicitly taught and fails on spellings "
      "it was not. Its false interventions are the expensive kind: it rewrites the fruit.\n"
      "- `phonetic` generalises to unseen spellings *and* declines to act when the "
      "evidence does not support it. Both halves are the product.\n")

    w("\n## Per-category results (phonetic)\n")
    by_cat: dict[str, list[dict]] = {}
    for r in recs:
        by_cat.setdefault(r["category"], []).append(r)
    w("| category | passed | total |")
    w("|---|---:|---:|")
    for cat in sorted(by_cat):
        rs = by_cat[cat]
        w(f"| `{cat}` | {sum(1 for r in rs if r['passed'])} | {len(rs)} |")

    failures = [r for r in recs if not r["passed"]]
    w(f"\n## Failures ({len(failures)})\n")
    if not failures:
        w("None.\n")
    for r in failures:
        tag = " *(known_hard, expected)*" if r["known_hard"] else " **(unexpected)**"
        w(f"### `{r['id']}`{tag}\n")
        w(f"- outcome: `{r['outcome']}`")
        w(f"- input: `{r['inputs']['formatted']}`")
        w(f"- expected: `{r['expected']}`")
        w(f"- actual: `{r['strategies']['phonetic']['actual']}`")
        w(f"- why: {r['rationale']}")
        if r["decision_trace"]:
            w(f"- system said: {r['decision_trace'][0]['reason']}")
        w("")

    lc = [r for r in recs if r.get("status_check")]
    if lc:
        w("## Lifecycle state checks\n")
        w("| case | expected status | actual | ok |")
        w("|---|---|---|:-:|")
        for r in lc:
            sc = r["status_check"]
            w(f"| `{r['id']}` | {json.dumps(sc['expected'])} | {json.dumps(sc['actual'])} "
              f"| {'yes' if sc['passed'] else 'NO'} |")
        w("")

    lat = payload["latency"]
    w("## Cost, latency and storage\n")
    w(f"**Model calls: 0. Monetary cost: Rs 0.00.** The resolution path is deterministic "
      f"and makes no network request, so these are exact rather than estimated.\n")
    w(f"\nLatency, warm, {lat['repeats']} repetitions after a discarded warm-up, measured "
      f"on {payload['machine']['platform']} / Python {payload['machine']['python']}:\n")
    w("| p50 | p95 | max | mean |")
    w("|---:|---:|---:|---:|")
    w(f"| {lat['p50_ms']} ms | {lat['p95_ms']} ms | {lat['max_ms']} ms | {lat['mean_ms']} ms |")
    w("\nMean time per stage. These sum to less than the p50 above because the outer "
      "measurement also covers writing the decision trace to the database and committing it, "
      "which happens after the stage timers stop:\n")
    w("| stage | ms |")
    w("|---|---:|")
    for k, v in lat["stage_means_ms"].items():
        w(f"| {k} | {v} |")
    w("\nDatabase growth with ordinary use. Growth is sub-linear because repeated "
      "observations reinforce existing entries rather than creating new ones, and context "
      "terms are capped at 25 per entry. The floor is the 414-row common-word guard list:\n")
    w("| observations | entries | rows | size |")
    w("|---:|---:|---:|---:|")
    for p in payload["db_growth"]:
        w(f"| {p['observations']} | {p['entries']} | {p['total_rows']} | {p['kb']} KB |")

    w("\n## Reproducibility\n")
    w("Per-case artifacts under `eval/results/cases/` contain no timing data and are "
      "byte-identical across runs on the same commit. After re-running this harness, "
      "`git diff -- eval/results/cases` should report **no changes at all**; only the "
      "measured latency numbers in `results.json` and in this file will move. Timing is "
      "reported once, from the dedicated warm-path measurement above, rather than as 44 "
      "noisy single-shot samples.\n")

    w("\n## Where to look next\n")
    w("Every case has a full artifact in `eval/results/cases/<id>.json` containing the "
      "inputs, the expectation, what each strategy actually produced, the memory state at "
      "decision time, and the reason the system gave for every span it considered.\n")

    (RESULTS_DIR / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    CASE_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for stale in CASE_RESULTS_DIR.glob("*.json"):
        stale.unlink()

    cases = load_cases()
    print(f"running {len(cases)} cases x {len(baselines.STRATEGIES)} strategies ...")
    records = []
    for i, case in enumerate(cases, 1):
        rec = run_case(case)
        records.append(rec)
        (CASE_RESULTS_DIR / f"{rec['id']}.json").write_text(
            json.dumps(rec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        mark = "ok  " if rec["passed"] else ("hard" if rec["known_hard"] else "FAIL")
        print(f"  [{i:>2}/{len(cases)}] {mark}  {rec['id']}")

    print("measuring latency ...")
    latency = latency_profile()
    print("measuring database growth ...")
    growth = db_growth_curve()

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "aggregate": aggregate(records),
        "latency": latency,
        "db_growth": growth,
        "model_calls": 0,
        "cost_inr": 0.0,
        "known_hard_count": sum(1 for r in records if r["known_hard"]),
        "cases": records,
    }
    (RESULTS_DIR / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_summary(payload)

    if EVAL_DB.exists():
        EVAL_DB.unlink()

    a = payload["aggregate"]["phonetic"]
    unexpected = [r for r in records if not r["passed"] and not r["known_hard"]]
    print(f"\n{a['passed']}/{a['total']} passed  "
          f"(precision {a['precision']}, recall {a['recall']}, f1 {a['f1']})")
    print(f"useful {a['useful_intervention']}  false {a['false_intervention']}  "
          f"missed {a['missed_intervention']}  wrong {a['wrong_intervention']}")
    print(f"results written to eval/results/")
    if unexpected:
        print(f"\nUNEXPECTED FAILURES ({len(unexpected)}):")
        for r in unexpected:
            print(f"  {r['id']}: expected {r['expected']!r} got "
                  f"{r['strategies']['phonetic']['actual']!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
