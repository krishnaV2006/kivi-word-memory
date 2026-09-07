# RUN.md

## Primary review method: **completely local application**

Python + SQLite, running on your machine. **No API key, no network access, no Docker, no
Node, no build step.** Three dependencies. The database is a file in the repository root.

Nothing in this repository calls a model or an external service, so there is nothing to
configure before it will work.

---

## 1. Required runtimes and versions

| Requirement | Version |
|---|---|
| Python | **3.10 or newer** (developed and measured on 3.12.5) |
| pip | any version shipped with the above |

Nothing else. No Node, no Docker, no database server — SQLite ships with Python.

Check what you have:

```bash
python --version
```

If that prints Python 2.x or "command not found", use `python3` in place of `python`
throughout this file.

## 2. Required environment variables

**None.** The application runs with no environment configuration at all.

One optional variable exists:

| Variable | Default | Purpose |
|---|---|---|
| `KIVI_DB_PATH` | `./kivi.db` | Move the SQLite file elsewhere. The evaluation sets this itself to avoid touching your demo data. |

`python -m app.db` also accepts `reindex`, which recomputes phonetic keys from stored
surfaces. You do not need it for a fresh setup — only when upgrading a database created
before migration 003 added the fuzzy key tier.

There is no `.env.example` because there is no `.env`. No credential of any kind is read
by this codebase.

## 3. Install dependencies

From the repository root:

**macOS / Linux**
```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

**Windows (PowerShell)**
```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt
```

Installs exactly three packages: `fastapi`, `uvicorn`, `jellyfish`.

## 4. Create, migrate and seed the database

```bash
python -m app.db migrate
python -m seed.seed
```

Expected output:

```
applied 001_init.sql
applied 002_context_terms.sql
applied 003_fuzzy_keys.sql
seeded 20 observations -> 9 active, 1 candidate
loaded 414 common words for the homophone guard
```

Both commands are idempotent — running them twice is safe.

To see what exists at any point:

```bash
python -m app.db status
```

## 5. Start every required process

One process:

```bash
python -m uvicorn app.main:app --port 8000
```

Leave it running. There is no worker, queue, or second service.

## 6. Interface to open

**http://127.0.0.1:8000**

No credentials. The page has three panels: **Transcribe** (top left), **Teach** (bottom
left), **Memory state** (right).

## 7. Primary interactions to try

Each of the six things the brief asks a reviewer to be able to do, in order. The
Transcribe panel has one-click `try:` links that fill both input boxes for you.

**a. See the memory-aware result, and why.** Click **try: The brief's example**, then
**Resolve → memory-aware**. `Ask Aditya to review the Sarvam Kiwi service.` becomes
`Ask Aaditya to review the Sarvam Kivi service.` with the changes highlighted. Below it,
every span the system considered and the reason it did or did not act — including
`Sarvam`, which it left alone because it was already correct.

**b. Watch it generalise to a spelling it has never seen.** Click
**try: A spelling never observed** and resolve. `Adithya` becomes `Aaditya`. The system
was only ever taught `aditya`. Look at the Memory state panel: the `heard as:` line for
Aaditya does not contain "adithya". The match came from the phonetic key, not a stored
string.

**c. Watch it refuse.** Click **try: The fruit, not the product** and resolve.
`I ate a kiwi for breakfast.` is left completely alone, and the trace says why: *"kiwi is
an ordinary English word and nothing in this sentence belongs to Kivi"*. Then try
**try: Inside an email address** and **try: Two people who sound alike** — three
different reasons for doing nothing.

**d. Provide an observation and watch memory change.** In the **Teach** panel, on the
**Correction** tab, enter `nandhini` → `Nandini`, kind `person`, and record it. The
Memory state panel gains a **candidate** entry — it will not act yet. Record the same
correction a second time. It becomes **active**. Now resolve
`Nandhinee is on the call.` and it becomes `Nandini is on the call.` — a third spelling
neither correction contained.

**e. Watch it stop.** In **Teach → Revert**, enter `Bulbul` reverted to `Bulbool`, twice.
The entry turns **suppressed** in the memory panel. Resolve
`Bulbool has thirty nine voices.` — unchanged, and the trace explains that the entry was
reverted by the user and will not act.

**f. Reset.** Click **Reset to seed** in the top right. Memory returns to the seeded
state. (Command-line equivalent in section 10.)

**Also worth trying**, since Kivi is built for Indian users and these are the least
obvious behaviours:

- **try: Hinglish** — a learned name and a learned product inside a Hindi sentence.
  Nothing in the design is English-specific.
- **try: Hinglish, but the fruit** — `Main kiwi kha raha hoon` is left alone. The guard
  does not degrade when the carrier language changes.
- **try: Devanagari — never taught in this script** — `आदित्य` becomes `Aaditya`. That
  word was only ever taught in Latin script; Devanagari is transliterated into the same
  phonetic skeleton, so one correction covers both scripts.
- **try: Devanagari, but the fruit** — and the guard still holds there.
- **try: b/v drift, with context** vs **b/v drift, no context** — the same span and the
  same memory, decided differently. The over-permissive b/v tier only fires when the
  sentence independently supports it.

## 8. Run the evaluation

Stop the server first, or open a second terminal. From the repository root, with the
virtual environment active:

```bash
python -m eval.run_eval
```

Takes roughly 1–2 minutes. It runs the 45 labelled cases across three strategies, then
an adversarial false-positive pass over ~1,600 generated sentences in English,
Hinglish and Devanagari. Both create their own
scratch databases (`eval/.eval.db`, `eval/.adversarial.db`), rebuild them from scratch,
and delete them when finished. **Your demo database is not touched.**

Expected final output:

```
55/56 passed  (precision 1.0, recall 0.9677, f1 0.9836)
useful 30  false 0  missed 1  wrong 0
```

The one failure is `codemix-devanagari-should-fire`, marked `known_hard` in the dataset
and expected to fail; it is explained in README.md and DISCOVERIES.md §12.
It also prints the adversarial result (1616 sentences, 0 interventions) and notes that
one decision branch, `abstain_low_score`, is never exercised — that is expected and is
explained in DISCOVERIES.md §9. If any case fails, the run prints an
`UNEXPECTED FAILURES` block listing the case, the expectation and the actual output.

A high score on a dataset written by the submitter is worth distrusting, so README.md
explains how each of the two cases that once failed was closed, and the adversarial suite
in `eval/results/adversarial.md` tests the property the curated cases cannot: that memory
stays out of the way of 1,616 sentences it was never taught anything about.

### Optional: the unit tests

```bash
python -m tests.test_phonetics
python -m tests.test_validation
```

Plain asserts, no pytest — one fewer dependency to install. The first covers the phonetic
layer, the second covers input validation.

### Optional: check the documentation against the results

```bash
python -m eval.check_docs
```

Verifies that every number claimed in README.md and RUN.md matches
`eval/results/*.json`, and exits non-zero if any is stale. This repository makes a lot of
numeric claims and they were all true when written; this is how you can tell they still
are without re-deriving them by hand.

### Optional: the scale measurement

Not part of the main run, because it takes 3–5 minutes:

```bash
python -m eval.scale
```

It fills memory with up to 10,000 synthetic entries and reports latency, how many
candidates a span retrieves, and whether the seeded assertions still hold at each size.
Results are written to `eval/results/scale.md` and are committed, so you can read them
without running it.

## 9. Where evaluation results are written

| Path | Contents |
|---|---|
| `eval/results/summary.md` | **Start here.** Ablation table, per-category results, every failure, adversarial summary, decision-branch coverage, latency, cost, database growth. |
| `eval/results/adversarial.md` | False-positive stress test: ~1,600 sentences containing no memory term, plus a control group that must fire. |
| `eval/results/scale.md` | Latency, retrieval breadth and correctness from 10 to 10,000 entries. Written by a separate command, see below. |
| `eval/results/results.json` | The same data as machine-readable JSON, including every case. |
| `eval/results/cases/<case-id>.json` | One file per case: inputs, expected, what all three strategies actually produced, the memory state at decision time, and the reason for every span considered. |

These files are committed to the repository, so you can compare a fresh run against the
committed results to confirm reproducibility.

Good individual files to open:
`eval/results/cases/fire-unseen-variant-adithya.json` (the central claim),
`eval/results/cases/noop-kiwi-fruit.json` (the refusal), and
`eval/results/adversarial.md` (the claim that it stays out of the way at scale).

## 10. Reset procedure

**From the interface:** click **Reset to seed**, top right.

**From the command line:**

```bash
python -m app.db reset
python -m seed.seed
```

`app.db reset` deletes the SQLite file and re-applies both migrations; `seed.seed`
replays the seed observations. The result is byte-equivalent in content to a first-time
setup. Both are safe to run repeatedly.

To remove everything including the database file:

```bash
python -m app.db reset
```

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'app'`** — run commands from the repository root,
not from inside `app/` or `eval/`.

**Port 8000 in use** — pass `--port 8001` to uvicorn and open that port instead.

**`python` is Python 2** — use `python3` everywhere in this file.
