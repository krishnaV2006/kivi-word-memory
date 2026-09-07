# What we found while building this

The brief asks to see the cases we discovered and what became possible because of the
abstraction. These are the findings that changed the design, each linked to the case that
now pins it. Most of them are things we got wrong first.

**The numbers in each section are what was measured at the time of that finding**, and
are deliberately not updated afterwards — a finding that says "false positives stayed at
0 across 1,598 sentences" is a record of the evidence that justified a decision when it
was made. Current numbers are in `eval/results/summary.md`, and
`python -m eval.check_docs` verifies that README.md and RUN.md agree with them.

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

**First consequence — refused.** For several commits we did not implement it, and
`fire-known-hard-bekariya` sat in the dataset as a documented **failure**. Given that this
system's entire argument is that a false intervention costs more than a miss, catching
this one by widening the net would have contradicted the thesis. The section then said:

> The right fix is not a rewrite rule. It is a second, much more permissive retrieval tier
> that is only allowed to fire with strong context support — the same shape as the
> common-word guard.

**Second consequence — built.** That tier now exists. `fuzzy_skeleton()` folds b/v/w and
is indexed as a fourth key algorithm, `indic_fuzzy`, behind migration 003. Retrieval by
that key is free; *surviving* it is not. A fuzzy candidate carries a heavier penalty
(0.80 against the loose tier's 0.85) and is dropped outright unless the sentence supplies
independent context support:

```python
if route == "indic_fuzzy" and boost <= 0.0:
    continue
```

The result is the distinction that makes the tier safe rather than reckless:

| input | result | why |
|---|---|---|
| `Krishna Bekariya submitted the form.` | → `Vekariya` | `krishna`, `submitted` are Vekariya context terms |
| `Bekariya is here.` | unchanged | same span, same memory, no supporting context |

**What made this safe to ship was the adversarial harness, not the argument.** The reason
this fix was not attempted earlier is that there was no way to know what widening
retrieval cost. There is now: after enabling the tier, the false-positive suite still
reports **0 interventions across 1,598 sentences**, including 199 real personal names, and
exactly **one** case artifact in the whole evaluation changed — the one this targeted.
The evidence is a diff, not a claim.

The dataset gained `noop-fuzzy-tier-needs-context` at the same time, because a permissive
tier tested only in the direction that flatters it is not tested at all.

**Pinned by:** `fire-known-hard-bekariya`, `noop-fuzzy-tier-needs-context`,
`tests/test_phonetics.py::test_fuzzy_tier_folds_b_and_v_but_only_there`,
`eval/results/adversarial.md`

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

---

## 11. A test that only proves a negative can be worthless

**Found:** by building an adversarial harness to check the claim the curated dataset
cannot support. Forty-five cases prove the forty-five situations their author imagined.
They say nothing about whether memory quietly corrupts ordinary text at scale — and that
is the failure this whole system is built to avoid.

So: 1,600 sentences containing nothing the user has ever taught Kivi, including 200 real
personal names across five sentence frames. The names corpus is the sharp one, because
most of those names are Indian and therefore sit in exactly the phonetic neighbourhood the
skeleton rules were tuned for, and the loose consonant key retrieves candidates among them
constantly. Any rewrite renames a real person.

The first run flagged five interventions, all of them the name `Aditya`. That is not a
false positive — `Aditya` **is** Aaditya, the user's colleague, and rewriting it is the
product working. The mistake was mine: I had put one of the user's own memory terms into a
list labelled "names not in memory".

**Consequence:** rather than delete the name, we turned the accident into a **control
group**, and it fixed a real weakness in the harness. A test whose only possible result is
"nothing happened" cannot distinguish a well-behaved system from a broken one that never
fires at all. If the resolver silently stopped working, the false-positive suite would go
green.

Names are now split automatically on strict phonetic skeleton:

| corpus | expectation | result |
|---|---|---|
| neutral, names, homophone | must not fire | **0 / 1598** |
| names_control | **must** fire | **5 / 5** |

The control recall is what gives the zero its meaning. The split is computed, not curated,
so it cannot be quietly gamed by moving an inconvenient name across the line — and names
that merely share a *loose* key with a memory deliberately stay in the scored corpus,
because those near-misses are the entire point of running this at all.

**Pinned by:** `eval/results/adversarial.md`, `eval/adversarial.py::split_names`

---

## 12. Cross-script memory, and the false positive it immediately introduced

**Found:** by asking what a monolingual dataset was failing to test. Kivi is built for
Indian users and Saaras emits code-mixed output, yet every case in this repository was
English. Two probes, and two different answers.

**Hinglish worked with no changes at all.** `Kal Aditya ke saath meeting hai.` corrects to
`Aaditya`, and `Main kiwi kha raha hoon.` — *I am eating a kiwi* — is correctly left
alone. Nothing in the design was ever English-specific; matching is on phonetic skeletons
of Latin tokens, so the carrier language is irrelevant. Eight cases now record that,
because a capability nobody tested is a capability nobody can rely on.

**Devanagari produced nothing.** `normalize()` kept only `[a-z]`, so आदित्य yielded no
keys and memory was simply inert. For an Indian-language-first product that is the most
consequential thing the system could not do: a user who teaches Kivi a word in English
gets nothing back when they write it in Hindi.

Transliteration into the skeleton alphabet closes it, and the interesting part is a rule
that has to be conditional. Hindi deletes the word-final inherent vowel, so सर्वम is
*sarvam*, not *sarvama* — but the vowel survives after a conjunct, which is why आदित्य is
*aaditya* and not *aadity*. Delete unconditionally and the headline name stops matching;
never delete and सर्वम, चिन्मय and बाज़ार all stop matching. The rule is "delete the final
schwa unless the consonant closed a conjunct", and with it seven of eight test terms land
on exactly the key their Latin spelling produces.

**Then it broke the guard.** The first end-to-end run rewrote
**मैं कीवी खा रहा हूँ** — *I am eating a kiwi* — into the product name. The homophone
guard compared the raw span against an English word list, and कीवी is not in an English
word list, so the guard never even ran. A feature added for Indian users had made the
system worse for Indian users, in exactly the way this project claims to care about most.

The fix follows from what the guard is actually asking. "Is this an ordinary word" is a
question about **sound**, not spelling, so it now compares skeletons: `kiwi` and कीवी both
reduce to `kivi`, and the guard fires in either script.

**What the evidence says.** After both changes, no Latin case artifact changed at all, and
the adversarial suite — extended with a Devanagari corpus, five of whose sentences contain
the fruit — still reports **0 interventions across 1,616 sentences**.

One thing genuinely does not cross scripts, and `codemix-devanagari-should-fire` is left
failing to pin it: **identity crosses, context does not**. `कीवी सर्विस ठीक है` is not
corrected, because the supporting word सर्विस is a transliterated loanword reducing to
`sarvis` while the context term learned from English reduces to `servike`. The two never
meet. The behaviour is conservative rather than wrong — unambiguous names resolve in
Devanagari, homophones stay guarded — but it is a real gap, and closing it means learning
context terms per script.

**Pinned by:** `eval/cases/04_code_mixed.json` (10 cases),
`codemix-devanagari-should-fire`, the `devanagari` corpus in `eval/results/adversarial.md`

---

## 13. The scale test found an O(n) bug that no small-scale measurement could

**Found:** by replacing an assertion with a measurement. The README claimed this design
"would not be fine at 10,000 entries". That was a guess, so we filled memory with 10,000
synthetic Indian-style names — built from a syllable pool, so they sit in the same
phonetic space as the real entries rather than flattering the numbers by never colliding —
and measured.

| entries | cold p50 | warm p50 | max candidates per span |
|---:|---:|---:|---:|
| 10 | 6.7 ms | 2.1 ms | 2 |
| 1,000 | 42.5 ms | 40.4 ms | 2 |
| 10,000 | **594.4 ms** | **371.1 ms** | 2 |

Six hundred milliseconds for a dictation product is unusable. But look at the last
column: the number of candidates actually retrieved never moved. Retrieval was fine.
Something else was linear in the size of memory.

It was `build_memory_prompt_block`. To decide which memories were relevant to an
utterance it iterated **every entry**, and recomputed a phonetic skeleton for **every
surface** in memory, on every single request. At seed scale that is ten entries and
invisible. At ten thousand it dominated everything else in the system by an order of
magnitude.

The fix is that the answer was already indexed. The same `key_index` retrieval uses will
map a sentence's skeletons straight to entry ids, so the scan became a lookup — O(tokens)
instead of O(entries):

| entries | warm p50 before | warm p50 after |
|---:|---:|---:|
| 10 | 2.06 ms | 2.45 ms |
| 1,000 | 40.4 ms | 4.05 ms |
| 10,000 | 371.1 ms | **3.71 ms** |

All 56 case artifacts were byte-identical afterwards, so the behaviour is unchanged.

**What the corrected numbers actually say** is more useful than the bug. Two curves that
were tangled together are now separable:

- **Resolution is flat in the size of memory** — 2.45 ms at 10 entries, 3.71 ms at
  10,000. The retrieval design does scale.
- **Loading memory is linear** — cold p50 still reaches 233 ms at 10,000 entries, because
  the HTTP layer deliberately re-reads all of memory every request so that an observation
  recorded a moment ago cannot be missed.

So the honest version of limitation 2 is no longer "no cache, might be slow". It is: the
no-cache decision is free at seed scale and costs roughly 229 ms at 10,000 entries, and
here is the measurement. That is a trade-off a reviewer can argue with.

The narrow lesson: a latency number taken at one size tells you almost nothing about
which part of a system is expensive. This bug was invisible at every size the rest of the
evaluation ever exercised.

**Pinned by:** `eval/results/scale.md`, `eval/scale.py`

---

## 14. The inspection log was going to outgrow the memory it explains

**Found:** by asking which table grows with *use* rather than with *learning*. The
evaluation had been reporting database growth against number of observations, and that
curve is reassuringly flat — repeated corrections reinforce existing entries instead of
creating new ones, so 200 observations produce 10 entries.

But learning is the rare event. A user corrects a handful of words ever. **Resolution
happens every time they speak**, and each one wrote three rows to the decision trace,
forever:

| utterances | decision rows | database |
|---:|---:|---:|
| 100 | 300 | 104 KB |
| 1,000 | 3,000 | 532 KB |
| 5,000 | 15,000 | **2.5 MB** |

Still climbing linearly, and already an order of magnitude larger than everything the
system actually remembers. For a dictation product used all day this is days, not years.

**Consequence:** the fix follows from what the trace is *for*. It exists so a person can
ask "why did Kivi just do that" — a question about recent history. Nobody inspects why a
word was changed six months ago. So the trace became a ring buffer over the most recent
200 requests rather than a log, pruned by whole request so an inspectable trace is never
half-deleted, and behind a count check so the delete runs about once every 200
resolutions rather than on every one.

| utterances | decision rows | database |
|---:|---:|---:|
| 100 | 300 | 104 KB |
| 1,000 | 1,191 | 324 KB |
| 5,000 | 1,131 | **324 KB** |

Flat. And the evaluation now reports both curves, because measuring growth against the
rare event while the common event grows unbounded is how you get a graph that says
everything is fine.

**Pinned by:** the two growth tables in `eval/results/summary.md`,
`app/resolver.py::_prune_trace`

---

## 15. Acting on §9: the redundant threshold, removed

**Found:** §9 diagnosed that `APPLY_THRESHOLD` was nearly dead code — `SIM_FLOOR = 0.85`
rejected almost everything the threshold would have caught, leaving it reachable only in a
five-point window. That section ended by naming the better design without building it:

> a future version should either collapse the two thresholds into one or lower `SIM_FLOOR`
> and let the score do the work — which is the better design, because the score accounts
> for evidence and context and a raw similarity floor does not.

**Done.** `SIM_FLOOR` dropped from 0.85 to 0.60, demoting it from a decision-maker to
what it should always have been: a cheap filter that skips candidates not worth scoring.
The score now does the deciding.

The measurements say this cost nothing and bought three things:

| | before | after |
|---|---|---|
| cases | 55/56, precision 1.0 | 55/56, precision 1.0 |
| adversarial | 0 / 1,616 | 0 / 1,616 |
| case artifacts changed | — | **1** |
| branch coverage | 7 of 8 | **8 of 8** |
| warm p50 @ 10k entries | 2.6 ms | 1.9 ms |

The single artifact that changed is `noop-cave-loose-key-collision`, and it changed for
the better. `cave` still is not rewritten, but the reason improved from *no candidate
shares a phonetic key* — which was false, one does — to:

> closest memory 'Kivi' scored 0.599, below the 0.72 apply threshold

That is the honest account of what happens. The loose key genuinely does retrieve `Kivi`
for `cave`, and the system genuinely does consider and reject it. Hiding that behind a
similarity floor made the trace *less* truthful about the system's own behaviour, which
for a component whose whole job is explaining itself is the wrong trade.

And with the branch finally reachable, `abstain_low_score` is exercised by a real case
rather than reported as a gap.

**Pinned by:** `noop-cave-loose-key-collision`, the branch-coverage table in
`eval/results/summary.md`

---

## 16. The adversarial harness was silently testing the wrong database

**Found:** by adding a second persona. Everything in this repository had been evaluated
against one user, which invites the obvious question — is the mechanism general, or was
it tuned to Krishna's names? So a case group builds a completely different user: a
designer whose colleague is `Miira` and whose design system is called `Page`, a homophone
that did not exist when the guard was written.

The mechanism generalised exactly as claimed. But the adversarial suite, which had
reported **0 false positives** on every run for a dozen commits, suddenly reported **6**.

The cause was mine, and it was in the test harness rather than the product.
`eval/adversarial.py` claimed its scratch database with:

```python
os.environ.setdefault("KIVI_DB_PATH", str(ADV_DB))
```

`setdefault` does nothing if the key is already set. Run standalone that is fine. But
`run_eval` sets `KIVI_DB_PATH` to the *evaluation's* database before importing it, so
when the adversarial harness ran as part of the main evaluation it resolved all 1,626
sentences against whatever state the final evaluation case happened to leave behind —
and then seeded the real persona on top of that.

For a dozen commits this was harmless, because the leftover state was close enough to the
seed that the numbers came out identical. Adding a persona named `Miira` broke the
coincidence: `Meera` is in the names corpus, so five sentences correctly matched a memory
that should never have existed in that run, and the suite reported them as false
positives.

**Consequence:** `run()` now claims `KIVI_DB_PATH` explicitly for the duration and
restores the caller's value afterwards. Standalone and embedded runs now agree: 0 across
1,626.

Two things worth taking from it. First, `setdefault` is the wrong primitive for claiming a
resource — it silently defers to whoever got there first, which is precisely the case you
need to override. Second, and more uncomfortable: **the false-positive number had been
wrong, in the safe direction, and nothing caught it.** It took a new test whose data
happened to collide with existing test data. Test isolation is not something to assert
once and stop checking, and a harness measuring a property this load-bearing deserved a
run that verified it produced the same answer both ways — which is now how it is checked.

**Pinned by:** `eval/adversarial.py::run`, `eval/cases/05_second_persona.json`

---

## 17. A latency number that moved with what the harness did first

**Found:** by the documentation checker, which carries a sanity bound rather than a string
match for latency — *cold p50 must be single-digit milliseconds, or the README and the
results disagree*. It started failing at **25 ms** against a documented 4.8 ms.

The obvious reading was a performance regression. It was not. Measured standalone, the
same code on the same machine reported **6.0 ms**. The number only inflated when the
measurement ran as part of the full evaluation.

The first guess was the adversarial pass — thousands of resolutions immediately before,
leaving the machine busy. Moving the measurement ahead of it changed almost nothing:
18.9 ms. The actual cause was the case sweep. Sixty-two cases across three strategies
create and drop a SQLite database **186 times**, and a latency measurement taken after
that inherits the disk churn.

| when latency is measured | cold p50 |
|---|---:|
| after the case sweep and adversarial pass | 25 ms |
| after the case sweep only | 18.9 ms |
| **first, before anything touches the disk** | **7.2 ms** |
| standalone, in a clean process | 6.0 ms |

**Consequence:** the measurement now runs at the very start of `main()`, and the report
says so. The remaining ~1 ms gap against a clean process is honest residue and is not
worth chasing.

The lesson is not about SQLite. **A benchmark inherits the state of whatever ran before
it**, so where a measurement sits in a script is part of its methodology — and this
repository had already been caught twice asserting a latency cause instead of measuring
it (§10, §13). What is different here is that nobody noticed by reading: a machine check
with a crude bound caught it, on a number a human eye would have skimmed past.

**Pinned by:** the latency section of `eval/results/summary.md`, `eval/check_docs.py`

---

## 18. The last failing case was two bugs, one of them plain English

**Found:** by refusing to leave `codemix-devanagari-should-fire` alone. It had sat as a
documented failure with the explanation that cross-script *identity* works while
cross-script *context* does not, which was true but incurious. `कीवी सर्विस ठीक है` was
not corrected because the supporting word `सर्विस` — the English word *service*, borrowed
into Hindi — never met the context term learned from English text.

Looking at why, the first bug was not about Hindi at all:

```
service  ->  servike
city     ->  kity
nice     ->  nike
price    ->  prike
```

Every bare `c` was mapped to `k`. But English `c` is an /s/ before `e`, `i` or `y`. The
skeleton had been quietly wrong about a large class of ordinary English words since the
first commit, and nothing caught it because no memory entry happened to contain a soft c.
It only surfaced through a Hindi loanword.

The naive fix introduced a second bug immediately:

```python
out.append("s" if nxt in "eiy" else "k")     # wrong
```

In Python `"" in "eiy"` is `True`, so every **word-final** `c` softened — `music` became
`musis`, `picnic` became `piknis`. A tuple fixes it. Both directions are now pinned by
tests, because this is exactly the kind of rule that looks obviously right in review.

With soft c fixed, `service` reduces to `servise` and `सर्विस` to `sarvis` — still not
equal on the strict key, but identical on the loose consonant key, `srvs`. So context
terms are now indexed under both.

**What made it safe to widen context** was the adversarial suite. Loosening context
matching is exactly the sort of change that buys one case and quietly costs several, and
before that suite existed the honest choice would have been to leave the case failing.
The measurement: **0 false positives across 1,626 sentences before the change, and 0
after.** The dataset went to 62/62.

The uncomfortable part is worth keeping in view. A phonetic rule wrong about `city`,
`nice` and `price` survived eighteen findings' worth of scrutiny because every test term
happened to avoid it. Coverage of the decision policy was measured; coverage of the
*alphabet* never was.

**Pinned by:** `tests/test_phonetics.py::test_soft_and_hard_c`,
`codemix-devanagari-should-fire`
