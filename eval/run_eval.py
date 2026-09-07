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


def _drop_eval_db() -> None:
    """Remove the scratch database and its write-ahead-log sidecars.

    Under WAL the main file alone is not the whole database, so unlinking only it would
    let one case inherit uncheckpointed pages from the previous one -- exactly the
    contamination fresh_db() exists to prevent.
    """
    for suffix in ("", "-wal", "-shm"):
        path = EVAL_DB.with_name(EVAL_DB.name + suffix)
        if path.exists():
            path.unlink()

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
    _drop_eval_db()
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

# Every outcome app/resolver.py can produce. Kept here deliberately so that adding a
# rule without adding a case for it shows up as an uncovered branch in the report.
POLICY_BRANCHES = {
    "apply",
    "noop_already_correct",
    "abstain_suppressed",
    "abstain_low_evidence",
    "abstain_low_score",
    "abstain_ambiguous",
    "abstain_common_word",
    "abstain_protected_span",
}


def branch_coverage(records: list[dict]) -> dict[str, int]:
    """How many cases reach each decision branch, counted once per case."""
    counts: dict[str, int] = {}
    for r in records:
        for action in {d["action"] for d in r["decision_trace"]}:
            counts[action] = counts.get(action, 0) + 1
    return counts


# --------------------------------------------------------------------------- measuring

def latency_profile(repeats: int = 40) -> dict:
    """Warm-path timing on a fixed sentence, after a discarded warm-up run.

    Two numbers, because they answer different questions:

      cold   memory read fresh from SQLite on every call -- what the HTTP layer does,
             and the number that describes the shipped product
      warm   one MemoryView reused across calls -- what a cache would buy, measured
             rather than asserted

    Measuring both is how we found that loading memory was never the bottleneck: the
    synchronous commit of the decision trace was. See README limitations.
    """
    from app.resolver import load_view

    case = {"skip_seed": False, "observations": []}
    conn = fresh_db(case)
    asr = "ask aditya to review the sarvam kiwi service"
    fmt = "Ask Aditya to review the Sarvam Kiwi service."
    journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    view = load_view(conn)

    def measure(use_view):
        resolve(conn, asr, fmt, view=use_view)          # warm-up, discarded
        xs, stages = [], []
        for _ in range(repeats):
            t0 = time.perf_counter()
            r = resolve(conn, asr, fmt, view=use_view)
            xs.append((time.perf_counter() - t0) * 1000)
            stages.append(r.timings_ms)
        xs.sort()
        pct = lambda p: round(xs[min(len(xs) - 1, int(len(xs) * p))], 3)  # noqa: E731
        return {
            "p50_ms": pct(0.50), "p95_ms": pct(0.95),
            "max_ms": round(max(xs), 3), "mean_ms": round(statistics.mean(xs), 3),
            "stage_means_ms": {
                k: round(statistics.mean(st[k] for st in stages), 3) for k in stages[0]
            },
        }

    cold, warm = measure(None), measure(view)
    conn.close()
    return {
        "repeats": repeats,
        "journal_mode": journal,
        "cold": cold,
        "warm": warm,
        # Kept at the top level so older readers of results.json still find a headline.
        "p50_ms": cold["p50_ms"], "p95_ms": cold["p95_ms"],
        "max_ms": cold["max_ms"], "mean_ms": cold["mean_ms"],
        "stage_means_ms": cold["stage_means_ms"],
    }


def trace_growth_curve(points=(100, 1000, 5000)) -> list[dict]:
    """How storage grows with *use* rather than with learning.

    Two different growth questions, and the second is the one that bites. Learning is
    rare -- a user corrects a handful of words. Resolution happens every time they speak,
    and each one writes a decision trace. Left unbounded that dominates the database
    within days, so the trace is a ring buffer over requests; this measures that it holds.
    """
    from app.resolver import resolve as _resolve

    _drop_eval_db()
    db_mod.migrate(verbose=False)
    conn = db_mod.connect()
    run_seed(conn, verbose=False)
    asr = "ask aditya to review the sarvam kiwi service"
    fmt = "Ask Aditya to review the Sarvam Kiwi service."

    curve, n = [], 0
    for target in points:
        while n < target:
            _resolve(conn, asr, fmt)
            n += 1
        stats = db_mod.db_stats(conn)
        curve.append({
            "utterances": n,
            "decision_rows": stats["rows"].get("decisions", 0),
            "total_rows": stats["total_rows"],
            "kb": round(stats["bytes"] / 1024, 1),
        })
    conn.close()
    _drop_eval_db()
    return curve


def db_growth_curve(points=(10, 50, 200)) -> list[dict]:
    """How storage grows with ordinary use. Observations are recycled from the seed."""
    seed_obs = json.loads((REPO_ROOT / "seed" / "seed.json").read_text(encoding="utf-8"))["observations"]
    curve = []
    for n in points:
        _drop_eval_db()
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
    w(f"Same {len(recs)} cases, three strategies. `exact_dict` is whole-word replacement of every "
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
    w(f"\nLatency over {lat['repeats']} repetitions after a discarded warm-up, on "
      f"{payload['machine']['platform']} / Python {payload['machine']['python']}, "
      f"SQLite journal mode `{lat['journal_mode']}`. Measured at the very start of the "
      f"run, before the case sweep: taken afterwards it reads three to four times higher, "
      f"because the sweep creates and drops a database for every case and every strategy "
      f"and the measurement inherits that disk churn.\n")
    w("**cold** reads memory fresh from SQLite on every call — this is what the HTTP layer "
      "actually does, and is the number that describes the shipped product. **warm** reuses "
      "one `MemoryView` across calls, which is what a cache would buy, measured rather than "
      "asserted:\n")
    w("| | p50 | p95 | max | mean |")
    w("|---|---:|---:|---:|---:|")
    for label in ("cold", "warm"):
        d = lat[label]
        w(f"| {label} | {d['p50_ms']} ms | {d['p95_ms']} ms | {d['max_ms']} ms "
          f"| {d['mean_ms']} ms |")
    w("\nMean time per stage (cold). These sum to less than the p50 above because the outer "
      "measurement also covers writing the decision trace and committing it, after the stage "
      "timers stop — and that commit, not memory loading, turned out to be the dominant "
      "cost. See DISCOVERIES.md §10:\n")
    w("| stage | ms |")
    w("|---|---:|")
    for k, v in lat["cold"]["stage_means_ms"].items():
        w(f"| {k} | {v} |")
    w("\nDatabase growth with ordinary use. Growth is sub-linear because repeated "
      "observations reinforce existing entries rather than creating new ones, and context "
      "terms are capped at 25 per entry. The floor is the 414-row common-word guard list:\n")
    w("| observations | entries | rows | size |")
    w("|---:|---:|---:|---:|")
    for p in payload["db_growth"]:
        w(f"| {p['observations']} | {p['entries']} | {p['total_rows']} | {p['kb']} KB |")
    w("\nGrowth with **use** rather than with learning, which is the one that bites: "
      "learning is rare, but a decision trace is written every time the user speaks. "
      "Unbounded that reached 2.5 MB after 5,000 utterances and kept climbing, so the "
      "trace is a ring buffer over the most recent requests:\n")
    w("| utterances | decision rows | total rows | size |")
    w("|---:|---:|---:|---:|")
    for p in payload["trace_growth"]:
        w(f"| {p['utterances']} | {p['decision_rows']} | {p['total_rows']} | {p['kb']} KB |")

    adv = payload["adversarial"]
    w("\n## Adversarial false positives\n")
    w("The table above is scored on cases this author wrote. This one is not: it runs "
      "sentences that contain nothing the user has ever taught Kivi and asks whether "
      "memory stays out of the way. Full report in "
      "[adversarial.md](adversarial.md).\n")
    w(f"**{adv['total_sentences']} sentences, {adv['total_interventions']} interventions "
      f"(rate {adv['false_positive_rate']}).** Control group recall "
      f"{adv['control_recall']} — the pipeline does fire when it should, so the zero is "
      f"not an inert system.\n")
    w("| corpus | sentences | interventions |")
    w("|---|---:|---:|")
    for name, r in adv["by_corpus"].items():
        note = " *(control, expected to fire)*" if r.get("expects_interventions") else ""
        w(f"| {name}{note} | {r['sentences']} | {r['interventions']} |")

    w("\n## Decision-branch coverage\n")
    w("Every outcome the policy in `app/resolver.py` can produce, and how many cases "
      "reach it. A branch with no cases is a rule the evaluation does not actually "
      "test:\n")
    w("| decision | cases reaching it |")
    w("|---|---:|")
    for action, n in sorted(payload["branch_coverage"].items(), key=lambda kv: -kv[1]):
        w(f"| `{action}` | {n} |")
    uncovered = payload["uncovered_branches"]
    if uncovered:
        w(f"\n**Not exercised: {', '.join('`' + b + '`' for b in uncovered)}.** "
          f"These rules are implemented but untested by this dataset.\n")
    else:
        w("\nEvery branch is exercised by at least one case.\n")

    w("\n## Reproducibility\n")
    w("Per-case artifacts under `eval/results/cases/` contain no timing data and are "
      "byte-identical across runs on the same commit. After re-running this harness, "
      "`git diff -- eval/results/cases eval/results/adversarial.md` should report **no changes at all**; only the "
      "measured latency numbers in `results.json` and in this file will move. Timing is "
      f"reported once, from the dedicated warm-path measurement above, rather than as "
      f"{len(recs)} noisy single-shot samples.\n")

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

    # Latency is measured first, before anything else touches the disk. Taken later in
    # the run it read three to four times higher -- not because resolution got slower,
    # but because the case sweep creates and drops a database for every case and every
    # strategy, and the measurement inherits that I/O churn. A number that moves with
    # what the harness did beforehand is a number about the harness.
    print("measuring latency ...")
    latency = latency_profile()

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

    coverage = branch_coverage(records)
    missing = sorted(POLICY_BRANCHES - set(coverage))
    if missing:
        print(f"note: {len(missing)} policy branch(es) not exercised: {', '.join(missing)}")

    # The adversarial harness answers the question the curated dataset cannot: does
    # memory stay out of the way of text it has never been taught anything about?
    print("running adversarial false-positive harness ...")
    from eval import adversarial as adv

    adv_payload = adv.run()
    (RESULTS_DIR / "adversarial.json").write_text(
        json.dumps(adv_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    adv.write_report(adv_payload)
    # Belt and braces: the harness restores KIVI_DB_PATH itself, but this run owns it.
    os.environ["KIVI_DB_PATH"] = str(EVAL_DB)
    print("measuring database growth ...")
    growth = db_growth_curve()
    trace_growth = trace_growth_curve()

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "aggregate": aggregate(records),
        "latency": latency,
        "db_growth": growth,
        "trace_growth": trace_growth,
        "model_calls": 0,
        "cost_inr": 0.0,
        "known_hard_count": sum(1 for r in records if r["known_hard"]),
        "adversarial": {
            "total_sentences": adv_payload["total_sentences"],
            "total_interventions": adv_payload["total_interventions"],
            "false_positive_rate": adv_payload["false_positive_rate"],
            "control_recall": adv_payload["control_recall"],
            "by_corpus": adv_payload["by_corpus"],
        },
        "branch_coverage": coverage,
        "uncovered_branches": sorted(POLICY_BRANCHES - set(coverage)),
        "cases": records,
    }
    (RESULTS_DIR / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_summary(payload)

    _drop_eval_db()

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
