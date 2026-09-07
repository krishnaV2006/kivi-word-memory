# The Words Kivi Keeps

Word-level phonetic memory for Kivi: the part of a personal system that learns the names,
products and terms belonging to one person, and uses them without being asked twice.

**To run it, see [RUN.md](RUN.md).** This file explains what it is and why it is shaped
this way.

```
ASR output        ask aditya to review the sarvam kiwi service
Formatted output  Ask Aditya to review the Sarvam Kiwi service.
Memory-aware      Ask Aaditya to review the Sarvam Kivi service.
```

---

## The product decision

**A memory entry is a phonetic identity, not a string replacement.**

That one sentence decides almost everything else. When the user corrects `aditya` to
`Aaditya`, the naive reading is that they have taught a substitution. The better reading
is that they have taught Kivi *how a name in their life sounds*, and that the correction
happens to be one piece of evidence for it.

The difference is testable. Teach the system `aditya → Aaditya`, then say a spelling it
has never encountered:

| input | exact-string dictionary | phonetic memory |
|---|---|---|
| `Adithya is joining the call.` | unchanged | **Aaditya** is joining the call. |
| `Adhitya reviewed the doc.` | unchanged | **Aaditya** reviewed the doc. |
| `Send Aadithya the numbers.` | unchanged | **Aaditya**, correct |

`th` is not a theta in romanised Indian names — it is an aspirated `t`. `dh` is an
aspirated `d`. `aa` and `a` are the same vowel. These are systematic, and a system that
knows them learns three spellings from one correction instead of one.

### The half that matters more: knowing when to do nothing

The brief's own example contains the trap. Kivi's name is an ordinary fruit. A memory
system that rewrites *"I ate a kiwi for breakfast"* into *"a Kivi"* is worse than no
memory at all, because it corrupts text the user never asked it to touch and it does so
invisibly.

So the system abstains, with a stated reason, in seven distinct situations:

| It does nothing when | Because |
|---|---|
| the word is ordinary English and nothing else in the sentence belongs to that memory | `kiwi` is usually a fruit |
| the span is inside an email, URL, @handle or backticked code | a rewritten address is a broken address |
| two memories are within a scoring margin of each other | `Aaditya` and `Adithya` can be two colleagues whose names sound identical; guessing renames one of them |
| the text is already correct | a no-op is not an intervention and must not be counted as one |
| the entry has only been seen once | one edit is a hypothesis, not a memory |
| the user has reverted it twice | they have told us to stop |
| nothing sounds close enough | memory must not invent |

Measured over 45 cases: **23 useful interventions, 0 false interventions.** The
exact-string dictionary baseline manages 12 useful and **10 false**.

## What the system learns from

Four kinds of evidence, in descending order of trust. All four are things that happen
during ordinary use — none requires the user to visit a settings page.

| Observation | What it means | Effect |
|---|---|---|
| `dictionary_add` | the user stated the word outright | active immediately, and pinned |
| `correction` | the user edited a transcript | one is a candidate, two make it active |
| `usage` | the user typed the word themselves | weak confirmation, and teaches context |
| `revert` | the user undid something we did | contradiction; two suppress the entry |

Two numbers govern the whole product, and both are deliberately small and visible in
`app/memory.py`:

- **Two confirmations before acting.** Acting on the first makes Kivi presumptuous —
  a single edit is indistinguishable from a typo or a change of mind. Waiting for a
  third makes it feel like it never learns.
- **Two contradictions before stopping.** Users undo things by accident.

**Suppressed, not deleted.** A deleted memory cannot explain itself. A suppressed one
still appears in the memory view and still reports why it declined to act, so the user
can see what Kivi believes and overrule it. A pinned entry — one the user added
explicitly — survives contradictions entirely: an inferred signal must never silently
overturn a direct instruction.

## Architecture

```
observations ──► memory.py ──► entries ─┬─ surfaces        (spellings seen)
  correction        lifecycle           ├─ phonetic_keys   (how it sounds)  ◄── the index
  dictionary_add    rules               ├─ evidence        (why we believe it)
  usage                                 └─ context_terms   (the world it lives in)
  revert                                             │
                                                     ▼
formatted text ──► resolver.py ──► retrieve ──► score ──► decide ──► apply.py ──► memory-aware
                                   (greedy)   (cautious)    │                     + decision trace
                                                            └──► reason, always
```

**Retrieval and decision are separate stages, with opposite temperaments.**

Retrieval is greedy. Three key algorithms cast a wide net over `phonetic_keys`:

| Key | Purpose | Example |
|---|---|---|
| `indic` | strict skeleton, vowels kept | `aditya`, `adithya`, `adhitya` → `aditia` |
| `indic_loose` | consonants only | `vekariya`, `vakaria` → `vkr` |
| `metaphone` | English phonetics, supplementary | recall net for non-Indic words |

The loose key deliberately over-generates — `cave` retrieves `Kivi`. That is fine. **A key
collision is not an intervention.** The decision stage is where the system is
conservative, and it rejects `cave` on similarity (0.63) long before anything is written.

Scoring is deliberately boring and inspectable:

```
score = similarity × (0.75 + 0.25 × confidence) + 0.10 × context_boost
```

Evidence *modulates* similarity rather than adding to it. An early additive version had a
real bug: a well-trusted entry could outrank a better-sounding one, and `Aditi` beat
`Aaditya` on the span `Adithya` purely on confidence. Multiplying fixes the ordering —
a trusted entry still has to actually sound like the span.

### Why there is no LLM in the resolution path

The brief does not ask for one, and every model call is a way for a reviewer's run to
fail. The deterministic path produces the memory-aware output on its own, which makes the
cost and latency numbers exact rather than estimated: **0 model calls, ₹0, ~4.8 ms.**

What memory owes a language model is still here and is a first-class output. Every
`/api/resolve` response includes `memory_prompt_block` — the memory context that would be
injected into Kivi's formatting prompt for that utterance, containing only the entries
relevant to it:

```
# Known personal terms
- Aaditya [person] (heard as: aditya)
- Kivi [product] (heard as: kiwi)
```

That is the seam where this system meets the rest of Kivi. Wiring it to a live model is a
provider call, not an architecture change.

## Evaluation

Full results: **[eval/results/summary.md](eval/results/summary.md)**. Run it yourself with
`python -m eval.run_eval` (see [RUN.md](RUN.md) §8).

**The dataset was authored and committed before the resolver existed.** The brief warns
that a collection of successful examples chosen after the system was built is not an
evaluation, so the git history is the evidence:

```
b23a652  Evaluation dataset and seed, authored BEFORE the resolver exists
8405bde  Memory lifecycle, resolver policy and rewrite guards
```

`git log --oneline --reverse` shows the order. The cases could not have been
reverse-engineered from a working system, because there was not one.

### Results

45 cases: 19 should-fire, 19 should-not-fire, 7 lifecycle. Every branch of the decision policy but one is exercised by at least one case; the exception is
documented above under limitations.

| metric | no memory | exact dictionary | phonetic memory |
|---|---:|---:|---:|
| cases passed | 20 / 45 | 22 / 45 | **44 / 45** |
| useful interventions | 0 | 12 | **23** |
| missed | 24 | 11 | **1** |
| false interventions | 0 | **10** | **0** |
| precision | 0.0 | 0.52 | **1.00** |
| recall | 0.0 | 0.50 | **0.96** |

The middle column is the honest strawman — whole-word replacement of every observed
spelling, which is what most people mean by "a dictionary". It is genuinely good at what
it was taught and blind to everything else, and its ten false interventions are the
expensive kind: it rewrites the fruit.

Every case has a full artifact at `eval/results/cases/<id>.json` with inputs, expectation,
all three strategies' output, the memory state at decision time, and the reason given for
every span considered.

### The adversarial result

A curated dataset only proves the cases its author imagined, and I wrote these 45. So
there is a second harness that proves the property most likely to be quietly false: that
memory stays out of the way of text it was never taught anything about.

**1,598 sentences containing no memory term. 0 interventions.** Full report:
[eval/results/adversarial.md](eval/results/adversarial.md).

| corpus | sentences | interventions | what it attacks |
|---|---:|---:|---|
| neutral | 585 | **0** | ordinary workplace sentences from everyday vocabulary |
| names | 995 | **0** | 199 real personal names that are not this user's |
| homophone | 18 | **0** | `kiwi` the fruit, `cave`, `Sarah`, `service` in non-product contexts |
| names_control | 5 | 5 | *control* — names that **are** the user's person; must fire |

The names corpus is the sharp one. Those are real names, mostly Indian, sitting in exactly
the phonetic neighbourhood the skeleton rules were tuned for, and the loose consonant key
retrieves candidates constantly among them. Any rewrite would rename a real person.

The control group is what makes the zero mean anything. A test that only proves a negative
is worthless if the pipeline is inert, so names that genuinely *are* the user's person
under another spelling are split out automatically — by strict phonetic skeleton, not by
hand — and must be rewritten. They are, 5 of 5.

### The failure

One case fails, and it is in the dataset on purpose.

`fire-known-hard-bekariya` — Indian ASR genuinely confuses `b` and `v`, so `Vekariya` can
come back as `Bekariya`. We do not map `b ↔ v`, because that rule would also collapse
`bat`/`vat`, `bet`/`vet` and `ban`/`van`. Given that this system's whole argument is that
false interventions cost more than misses, taking the miss is the consistent choice. It is
reported as a failure rather than quietly deleted.

`noop-sarah-real-name` was written expecting a *false* intervention and passes — `Sarah`
and `Saaras` share no phonetic key at all. The `known_hard` flag was removed once that was
measured, and the case's rationale records why.

## Limitations

Honest ones, in rough order of how much they would matter in production.

1. **The homophone guard leans on a curated 414-word list.** Context evidence is the
   better mechanism and does the real work in `fire-brief-example`, but for a word with no
   context support the static list is what stands between Kivi and rewriting the fruit. A
   name colliding with an ordinary word *outside* that list would not be protected. A
   frequency-ranked lexicon, or a part-of-speech signal, would be the correct fix.
2. **Memory is loaded per request with no cache.** The HTTP layer reads memory fresh on
   every call, because observations mutate it between requests and a stale-cache bug that
   silently applied a suppressed entry would cost more than the milliseconds. Measured
   cold p50 is 4.8 ms; reusing a `MemoryView` (what a cache would buy) is 2.5 ms, and the
   evaluation reports both. This limitation previously claimed memory loading dominated
   the request — that was wrong, and measuring it is how we found the real cost was the
   decision-trace commit. See DISCOVERIES.md §10.
3. **Context terms are a bag of words with no notion of recency or session.** In a real
   Kivi they should probably decay, and the active application would be a strong signal
   the model currently has no access to.
4. **Single-user.** No tenancy, no auth. Every table would need a user scope.
5. **Latin script only.** Devanagari and other Indic scripts are not handled; the
   skeleton rules assume romanised input. This matches Kivi's current output but not its
   ambition.
6. **`APPLY_THRESHOLD` is nearly redundant.** A branch-coverage table added to the
   evaluation showed it is never reached: `SIM_FLOOR` already rejects almost everything it
   would have caught, and it can only fire in a five-point similarity window on loose-key
   matches. Two knobs doing one job. Reported rather than papered over — see
   DISCOVERIES.md §9.
7. **The `usage` observation confirms every entry it mentions.** A user quoting someone
   else's text would strengthen memories they did not intend to.

## AI use

This repository was built with Claude (Claude Code) over a single working session, used as
an implementation partner rather than an autocomplete. The architecture, product
decisions, thresholds and evaluation design were specified and argued through in
conversation before code was written; Claude wrote most of the code and prose against
those decisions, and I directed, corrected and verified them.

Specific points where that mattered:

- The eval-before-implementation commit ordering was a deliberate choice to make the
  evaluation's independence verifiable, not just assertable.
- Measuring `metaphone("Aaditya") = TTY` against `metaphone("adithya") = AT0Y` early
  killed the plan to lean on an off-the-shelf phonetic algorithm and motivated the
  purpose-built Indic skeleton.
- Running the dataset surfaced a real bug rather than confirming a story: the
  already-correct check compared case-insensitively, so `Iitm` looked like it was already
  `IITM`. See DISCOVERIES.md.
- The decision to keep no live LLM call was made to protect the reviewer's run path, and
  is argued above rather than hidden.

Everything here has been run and verified from a clean clone.

## Repository map

| Path | What it is |
|---|---|
| `app/phonetics.py` | Indic skeleton, loose key, similarity |
| `app/memory.py` | Observation handling, lifecycle rules, memory state |
| `app/resolver.py` | Retrieval, scoring, decision policy, prompt block |
| `app/apply.py` | Protected spans, tokenisation, casing and morphology |
| `app/main.py` | HTTP API |
| `app/static/index.html` | The demonstration interface |
| `migrations/` | Schema as plain SQL, applied by `app/db.py` |
| `seed/seed.json` | Reproducible seed, expressed as observations |
| `eval/cases/` | 44 labelled cases |
| `eval/run_eval.py` | The harness (also runs the adversarial pass) |
| `eval/adversarial.py` | False-positive stress test over ~1,600 generated sentences |
| `eval/results/` | Committed generated results |
| `tests/test_phonetics.py` | `python -m tests.test_phonetics`, no pytest needed |
| `DISCOVERIES.md` | The failure modes found while building, linked to their cases |
