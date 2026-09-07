# What we found while building this

The brief asks to see the cases we discovered and what became possible because of the
abstraction. These are the findings that changed the design, each linked to the case that
now pins it. Most of them are things we got wrong first.

---

## 1. Metaphone splits the brief's own example

**Found:** before writing any matching logic, checking whether an off-the-shelf phonetic
algorithm would do.

```
metaphone("Aaditya")  ->  TTY
metaphone("adithya")  ->  AT0Y
```

They do not match. Metaphone is English-centric: it maps `th` to a theta, as in *think*.
But in romanised Indian names `th` is an **aspirated t** — "Adithya" is "Aditya" said with
more breath, not with a lisp. Metaphone also dropped the leading vowel in one form and
kept it in the other.

**Consequence:** the entire headline case of this task fails on Metaphone alone. This
killed the plan to lean on a standard algorithm and motivated `indic_skeleton()`, built
from the rewrite rules that actually govern romanisation: aspirate collapse
(`th→t`, `dh→d`, `bh→b`, `ph→f`), doubled-grapheme collapse (`aa→a`, `ee→i`, `oo→u`),
`w→v`, `z→j`, and the `ya`/`iya` glide.

Metaphone is still indexed as a third key, but only as a supplementary recall net.

**Pinned by:** `tests/test_phonetics.py::test_metaphone_is_insufficient_alone`,
`fire-unseen-variant-adithya`, `fire-unseen-variant-adhitya`, `fire-unseen-variant-aadithya`

---

## 2. One key cannot serve both precision and recall

**Found:** trying to make one skeleton handle both `adithya → aditia` and
`vakaria → vekariya`.

The Aditya family differs only in consonant aspiration and vowel doubling, so a
vowel-preserving skeleton unifies them cleanly. But `Vekariya` and `Vakaria` differ in an
**interior vowel** — `e` against `a` — which no consonant rule touches. Tightening the
vowel rules enough to unify those two started collapsing genuinely different names.

**Consequence:** two keys with different jobs. `indic` keeps vowels and is precise;
`indic_loose` drops everything but the initial letter and the consonants, and is
deliberately promiscuous — `vekariya` and `vakaria` both become `vkr`.

That reframed the architecture. **Retrieval and decision became separate stages with
opposite temperaments**: retrieval casts as wide a net as it likes, and scoring is where
the caution lives. Once a key collision stopped being treated as an answer, the loose key
became safe to add.

The cost is visible and tested: `cave` and `Kivi` both reduce to `kv`. The system
retrieves `Kivi` for `cave` on every sentence containing it, and rejects it every time on
similarity (0.63).

**Pinned by:** `fire-romanisation-vakaria`, `noop-cave-loose-key-collision`,
`tests/test_phonetics.py::test_romanisation_drift_needs_loose_key`

---

## 3. Confidence was outranking sound

**Found:** by running the dataset. The first scoring formula added evidence to similarity:

```
score = 0.65 × similarity + 0.20 × confidence + 0.15 × context     # wrong
```

On the span `Adithya`, this ranked **Aditi** (0.798) above **Aaditya** (0.795). Aditi was
added via `dictionary_add`, so it carried pinned confidence of 0.95, and that margin was
enough to beat a phonetically better match. The system would have renamed a colleague.

**Consequence:** evidence must *modulate* similarity, not compete with it.

```
score = similarity × (0.75 + 0.25 × confidence) + 0.10 × context   # right
```

A trusted entry still has to sound like the span. Trust breaks ties; it does not create
matches. We also made an exact skeleton match score a flat 1.0, categorically above any
fuzzy match, and gave loose-key-only matches a 0.85 penalty — which separated the two
candidates cleanly (0.94 against 0.79).

**Pinned by:** `fire-unseen-variant-adithya`, `noop-ambiguous-aditi`

---

## 4. "Already correct" is not a string comparison

**Found:** by an unexpected failure in the first full evaluation run.
`fire-acronym-casing` expected `I studied at Iitm.` → `I studied at IITM.` and got nothing.

The already-correct short-circuit compared case-insensitively: `"iitm" == "iitm"`, so the
span looked like it was already canonical. But **for an acronym the casing *is* the
memory.** Kivi knowing you write `IITM` and not `Iitm` is exactly the kind of small,
personal thing this system exists to hold.

**Consequence:** the check now asks the right question — *would applying this memory change
anything?* — by rendering the replacement and comparing:

```python
would_write = render_replacement(stem, canonical, kind)
if would_write == stem and status == "active":   # genuinely already correct
```

This is the discovery we would have missed entirely if the evaluation had been written
after the system. The case existed, failed, and was traced to a real defect.

**Pinned by:** `fire-acronym-casing`, `noop-already-correct`

---

## 5. The homophone guard cannot be phonetic

**Found:** immediately, and it is the deepest problem in the task.

`kiwi` and `Kivi` are not similar-sounding. They are **identical** — both skeletonise to
`kivi`, similarity exactly 1.0. No phonetic threshold can separate a product from a fruit,
because phonetically there is nothing there to separate.

**Consequence:** the guard has to come from somewhere other than sound, and we use two
signals that are not phonetics:

- a curated **common-word list** — if the span is ordinary English, require positive
  evidence rather than merely absent evidence against
- **context terms** mined from the sentences a memory has appeared in, matched
  *phonetically* so a term learned as `sarvam` still fires when the ASR writes `sarwam`

So `Ask Aditya to review the Sarvam Kiwi service.` rewrites `Kiwi` — `sarvam` and
`service` are both Kivi context terms — while `I ate a kiwi for breakfast.` does not, and
`We had toast, coffee and a kiwi.` does not either.

This is also the system's weakest joint, and README.md lists it first under limitations. A
personal name colliding with an ordinary word *outside* the 414-word list has no
protection.

**Pinned by:** `noop-kiwi-fruit`, `noop-kiwi-fruit-plural`, `noop-kiwi-fruit-food-context`,
`fire-brief-example`, `life-usage-mines-context`

---

## 6. Writing the cases first exposed a hole in the seed

**Found:** `fire-possessive-product` asserts that `Kiwi's dictionary needs a reset.`
becomes `Kivi's dictionary`. Under the guard in finding 5, that requires a Kivi context
term in the sentence — and the seed never mentioned Kivi's Dictionary at all.

**Consequence:** a `usage` observation was added to the persona's history
(*"The Kivi dictionary keeps the words I correct…"*). Note what changed and what did not:
the **seed** changed, the **case expectation** did not. Kivi genuinely has a Dictionary
feature, so a real user would have written that sentence.

Worth stating plainly, because it is the mechanism the eval-first ordering is for: writing
the assertions before the system made a gap in the *fixtures* visible as a failing case,
instead of the gap silently shaping what we later decided to claim.

**Pinned by:** `fire-possessive-product`

---

## 7. b ↔ v is real, and we still refuse to model it

**Found:** while assembling romanisation cases. Indian ASR genuinely confuses `b` and `v`
— `Vekariya` comes back as `Bekariya`, and a Bengali or Gujarati speaker may say either.

A `b ↔ v` rule would catch it. It would also collapse `bat`/`vat`, `bet`/`vet`,
`ban`/`van`, `boat`/`vote`.

**Consequence:** we do not implement it, and `fire-known-hard-bekariya` sits in the
dataset as a permanent, documented **failure**. Given that this system's entire argument
is that a false intervention costs more than a miss, catching this one by widening the net
would contradict the thesis.

The right fix is not a rewrite rule. It is a second, much more permissive retrieval tier
that is only allowed to fire with strong context support — the same shape as the
common-word guard. That is a real design, and it did not fit in the time available, so
the case is reported as failing rather than papered over.

**Pinned by:** `fire-known-hard-bekariya`

---

## 8. A case written to fail, that passes

**Found:** `noop-sarah-real-name` was authored expecting a false intervention — a colleague
named Sarah being renamed to the `Saaras` model — and marked `known_hard`.

It passes. `sarah` reduces to `srh` on the loose key, `saaras` to `srs`, and their strict
skeletons and Metaphone codes differ too. **They share no key at all**, so retrieval never
surfaces the model and no decision is ever needed.

**Consequence:** the `known_hard` flag was removed once measured, and the rationale in the
case file now records why it passes. The case stays, because that separation is a property
worth protecting against future changes to the skeleton rules — precisely the sort of
thing that a well-meant vowel-rule tweak could break.

A prediction that turns out wrong in the safe direction is still worth writing down.

**Pinned by:** `noop-sarah-real-name`

---

## 9. Two of our own thresholds overlap, and the coverage report found it

**Found:** by adding a decision-branch coverage table to the evaluation — every outcome
the policy can produce, and how many cases reach it. One branch came back at zero:
`abstain_low_score`.

The first instinct was that the dataset had a hole. It does not. The branch is very nearly
dead code, and the arithmetic says why. A candidate is discarded outright below
`SIM_FLOOR = 0.85`, and an active entry has at least two confirmations, so its confidence
is at least 0.75 and its score multiplier at least 0.9375:

| retrieval route | lowest score it can produce | can it fall below `APPLY_THRESHOLD = 0.72`? |
|---|---:|---|
| `indic` (exact skeleton) | 0.9375 | never — similarity is pinned at 1.0 |
| `metaphone` | 0.7969 | never — would need raw similarity < 0.768, already filtered |
| `indic_loose` | 0.6773 | only for raw similarity in **[0.85, 0.9035)** |

So `SIM_FLOOR` is doing almost all of the rejecting, and `APPLY_THRESHOLD` only ever fires
in a five-point window on loose-key matches.

**Consequence:** we left both in and reported the gap rather than inventing a case to
close it. Contriving an input that lands in a five-point window would have produced a
green coverage table and taught nobody anything. The honest reading is that the policy has
one redundant knob, and that a future version should either collapse the two thresholds
into one or lower `SIM_FLOOR` and let the score do the work — which is the better design,
because the score accounts for evidence and context and a raw similarity floor does not.

Worth noting what surfaced this: not a failing test, but asking the evaluation to report
which of its own rules it never exercised. A dataset can pass every case and still leave
branches of the policy completely untested.

**Pinned by:** the `Decision-branch coverage` table in `eval/results/summary.md`

---

## 10. We optimised the wrong thing first, and the measurement said so

**Found:** while acting on limitation 2 of the README, which asserted that loading memory
per request and the per-candidate SQL "dominate the ~13 ms".

The fix looked obvious: stop issuing a query per retrieval key and per candidate entry,
and load memory once into a `MemoryView`. That refactor was done, and it verifiably did
not change a single one of the 45 case results. Then we measured it.

| configuration | mean |
|---|---:|
| cold — fresh read, trace written | 12.8 ms |
| warm — view reused, trace written | 10.6 ms |
| warm — view reused, **no trace write** | 1.9 ms |

The view saved 2.2 ms. Removing the decision-trace write saved **8.7 ms**. The README's
stated cause was wrong: memory loading was never the bottleneck. The synchronous commit
of the inspection log was, because SQLite's default rollback journal fsyncs on every
commit and this system commits once per resolution.

**Consequence:** the actual fix was two lines in `app/db.py` — `journal_mode = WAL` and
`synchronous = NORMAL`:

| | before | after |
|---|---:|---:|
| cold p50 | 13.05 ms | **4.83 ms** |
| warm p50 | — | **2.45 ms** |

WAL also forced a correctness fix worth noting. Under WAL the `.db` file alone is no
longer the whole database, so both the reset procedure and the evaluation's per-case
teardown had to delete the `-wal` and `-shm` sidecars too. Deleting only the main file
would have let one evaluation case inherit uncheckpointed pages from the case before it —
precisely the contamination `fresh_db()` exists to prevent, reintroduced by a performance
change.

The `MemoryView` was kept, because it makes a decision a pure function of a snapshot and
it is what the adversarial harness reuses to run thousands of sentences. But it is now
reported for what it is: worth 2 ms, not 9. The evaluation prints cold and warm side by
side so the difference between "what we ship" and "what a cache would buy" is visible
rather than argued.

The lesson is narrow and unglamorous: a plausible story about where time goes is not a
measurement, and the README had confidently asserted one for several commits.

**Pinned by:** the `Cost, latency and storage` section of `eval/results/summary.md`
