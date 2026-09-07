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

## 8. Run the evaluation

Stop the server first, or open a second terminal. From the repository root, with the
virtual environment active:

```bash
python -m eval.run_eval
```

Takes roughly 30–60 seconds. It creates its own scratch database at `eval/.eval.db`,
rebuilds it from scratch for every case, and deletes it when finished. **Your demo
database is not touched.**

Expected final output:

```
44/45 passed  (precision 1.0, recall 0.9583, f1 0.9787)
useful 23  false 0  missed 1  wrong 0
```

The one failure is `fire-known-hard-bekariya`, which is marked `known_hard` in the
dataset and is expected to fail. It is documented in README.md and DISCOVERIES.md. If
any *unexpected* failure occurs, the run prints an `UNEXPECTED FAILURES` block listing
the case, the expectation and the actual output.

## 9. Where evaluation results are written

| Path | Contents |
|---|---|
| `eval/results/summary.md` | **Start here.** Ablation table, per-category results, every failure, latency, cost, database growth. |
| `eval/results/results.json` | The same data as machine-readable JSON, including every case. |
| `eval/results/cases/<case-id>.json` | One file per case: inputs, expected, what all three strategies actually produced, the memory state at decision time, and the reason for every span considered. |

These files are committed to the repository, so you can compare a fresh run against the
committed results to confirm reproducibility.

Good individual files to open:
`eval/results/cases/fire-unseen-variant-adithya.json` (the central claim) and
`eval/results/cases/noop-kiwi-fruit.json` (the refusal).

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
