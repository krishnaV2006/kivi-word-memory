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

So the system abstains, with a stated reason, in eight distinct situations:

| It does nothing when | Because |
|---|---|
| the word is ordinary English and nothing else in the sentence belongs to that memory | `kiwi` is usually a fruit |
| the span is inside an email, URL, @handle or backticked code | a rewritten address is a broken address |
| two memories are within a scoring margin of each other | `Aaditya` and `Adithya` can be two colleagues whose names sound identical; guessing renames one of them |
| the text is already correct | a no-op is not an intervention and must not be counted as one |
| the entry has only been seen once | one edit is a hypothesis, not a memory |
| the user has reverted it twice | they have told us to stop |
| nothing sounds close enough | memory must not invent |
| a b/v-folded match has no context support | that tier is over-permissive by design and must earn its place |

Measured over 62 cases: **34 useful interventions, 0 false interventions** — and, on a
separate adversarial suite, **0 false interventions across 1,626 sentences**. The
exact-string dictionary baseline manages 14 useful and **13 false**.

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

## How memories relate to one another

The brief names this as one of the decisions it will not specify, so here is the answer
and the reasoning behind it.

**Memories are not linked to each other directly. They are linked through the world they
share.** Every entry accumulates `context_terms` — the words it tends to appear near,
mined from the sentences the user actually wrote. Two memories are related when their
context overlaps, and a relation between two *specific* memories emerges for free when
one memory's context term happens to name another entry. In the seeded state that has
produced exactly two such links:

```
Kivi   → Sarvam    (weight 1.0)
Sarvam → Bulbul    (weight 1.0)
```

Nothing created those. They are what the persona happened to say.

This is deliberately weaker than a relation graph, and the reason is the brief's closing
instruction: *build the smallest one that makes Kivi feel as though it has met this
person before*. An explicit graph — entities, typed edges, co-reference — is the shape of
the largest system I could describe, and it would need its own learning rules, its own
decay, and its own failure modes, to buy something the context terms already deliver:

> `Ask Aditya to review the Sarvam Kiwi service.` → `Kiwi` becomes `Kivi`, because
> `Sarvam` and `service` are in Kivi's world.
> `I ate a kiwi for breakfast.` → untouched, because nothing in that sentence is.

That is the whole job a relation was needed for. The three limits are stated plainly: the
links are **undirected in effect but asymmetric in storage**, they are **unweighted by
recency**, and they carry **no type** — Kivi does not know that Sarvam is its employer
rather than its author. A real personal AI would need all three, and would earn them from
episodic and semantic memory, which the brief explicitly puts outside this task's edge.

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
| `indic_fuzzy` | consonants with b/v/w folded, **gated on context** | `vekariya`, `bekariya` → `bkr` |
| `metaphone` | English phonetics, supplementary | recall net for non-Indic words |

Devanagari is transliterated into the same skeleton alphabet before keying, so a
term taught once in Latin script is found in Hindi script. The rule that matters
there is conditional schwa deletion — सर्वम is *sarvam*, but आदित्य is *aaditya*,
because the final vowel survives after a conjunct.

The loose key deliberately over-generates — `cave` retrieves `Kivi`. That is fine. **A key
collision is not an intervention.** The decision stage is where the system is
conservative: `cave` is scored at 0.599 against a 0.72 threshold and rejected, and the
trace says exactly that rather than silently dropping it.

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
cost and latency numbers exact rather than estimated: **0 model calls, ₹0, ~7 ms cold / ~2 ms warm** on the machine in `summary.md`.

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

### On form factors

There is one interface here, because the brief asks for one. But the core is not coupled
to it, and that claim is load-bearing rather than aspirational: **the evaluation harness
is a second consumer of the same core**, and it never goes through HTTP. `eval/run_eval.py`,
`eval/adversarial.py` and `eval/scale.py` all call `app.resolver.resolve()` and
`app.memory.observe()` directly, against their own databases, with the web layer absent
entirely. `app/main.py` is a few lines per route over those functions.

So a CLI, a desktop client or a macOS host would consume the same three entry points. It
is not built, because a second interface is surface without new evidence — and the brief's
closing instruction argues against exactly that.

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

62 cases: 19 should-fire, 20 should-not-fire, 7 lifecycle, 11 code-mixed, 5 second-persona. Every branch of the decision policy but one is exercised by at least one case; the exception is
documented above under limitations.

| metric | no memory | exact dictionary | phonetic memory |
|---|---:|---:|---:|
| cases passed | 27 / 62 | 28 / 62 | **61 / 62** |
| useful interventions | 0 | 14 | **34** |
| missed | 35 | 19 | **1** |
| false interventions | 0 | **13** | **0** |
| precision | 0.0 | 0.48 | **1.00** |
| recall | 0.0 | 0.40 | **0.97** |

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

**1,626 sentences containing no memory term. 0 interventions.** Full report:
[eval/results/adversarial.md](eval/results/adversarial.md).

| corpus | sentences | interventions | what it attacks |
|---|---:|---:|---|
| neutral | 585 | **0** | ordinary workplace sentences from everyday vocabulary |
| shapes | 10 | **0** | paragraphs, markdown, ALL CAPS, quotes, numbers, degenerate input |
| devanagari | 18 | **0** | Hindi in native script, five containing the fruit कीवी |
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

### The one failure, and how the two earlier ones were closed

One case fails, and it is in the dataset on purpose. Two others used to fail and were
closed; neither was deleted, and the git history shows both.

**`codemix-devanagari-should-fire`** — the live failure. Identity crosses scripts but
context does not: `कीवी सर्विस ठीक है` is not corrected, because `सर्विस` is a
transliterated loanword reducing to `sarvis` while the context term learned from English
reduces to `servike`. The two never meet, so a homophone stays guarded in Devanagari even
when the sentence does support it. Conservative rather than wrong, and reported as a
failure rather than removed.

**`fire-known-hard-bekariya`** — Indian ASR genuinely confuses `b` and `v`, so `Vekariya`
comes back as `Bekariya`. For most of this project's life it failed, deliberately: a
global `b ↔ v` rule would also collapse `bat`/`vat`, `bet`/`vet` and `ban`/`van`, and
buying one miss with that many potential false positives contradicts the entire thesis.

It is now fixed, but not by widening the net. There is a fourth retrieval tier,
`indic_fuzzy`, which folds b/v/w and is then **required to earn its place**: a candidate
found only through it is discarded unless the sentence independently supplies context for
that memory. So `Krishna Bekariya submitted the form.` is corrected — `krishna` and
`submitted` are Vekariya context terms — and `Bekariya is here.` is left alone. The
dataset gained `noop-fuzzy-tier-needs-context` in the same commit, because a permissive
tier tested only in the direction that flatters it is not tested at all.

What made that safe to ship was the adversarial suite, not the reasoning. After enabling
the tier, false positives stayed at **0 across 1,598 sentences** and exactly **one** case
artifact in the entire evaluation changed — the one it targeted. That is a diff, not a
claim. Without that harness the honest choice would still have been to leave the case
failing.

**`noop-sarah-real-name`** was written expecting a *false* intervention and passed on the
first run — `Sarah` and `Saaras` share no phonetic key at all. The `known_hard` flag was
removed once that was measured, and the case's rationale records why it passes.

One branch of the decision policy is still never exercised, and the coverage table in
`summary.md` says so rather than hiding it. See limitation 6.

## Limitations

Honest ones, in rough order of how much they would matter in production.

1. **The homophone guard leans on a curated 414-word list.** Context evidence is the
   better mechanism and does the real work in `fire-brief-example`, but for a word with no
   context support the static list is what stands between Kivi and rewriting the fruit. A
   name colliding with an ordinary word *outside* that list would not be protected. A
   frequency-ranked lexicon, or a part-of-speech signal, would be the correct fix.
2. **Memory is loaded per request with no cache, and that is the one thing that does not
   scale.** The HTTP layer reads memory fresh on every call, deliberately: observations
   mutate it between requests and a stale-cache bug that silently applied a suppressed
   entry would cost far more than the milliseconds. Resolution itself is flat in the size
   of memory (about 2 ms at 10 entries, about 4 ms at 10,000), but loading is linear —
   cold p50 reaches roughly 230 ms at 10,000 entries. So the trade-off is free at seed scale and costs
   about 225 ms at 10,000 entries; the fix is a cache invalidated on write. See
   [eval/results/scale.md](eval/results/scale.md). Two earlier versions of this
   limitation were wrong about the cause, both times because they asserted instead of
   measuring — DISCOVERIES.md §10 and §13.
3. **Context terms are a bag of words with no notion of recency or session.** In a real
   Kivi they should probably decay, and the active application would be a strong signal
   the model currently has no access to.
4. **Single-user.** No tenancy, no auth. Every table would need a user scope.
5. **Cross-script identity works; cross-script context does not.** Devanagari is
   transliterated into the skeleton alphabet, so a word taught once in Latin is found in
   Hindi script — आदित्य resolves to `Aaditya` without ever being taught that spelling.
   But context terms do not cross: `सर्विस` is a transliterated loanword reducing to
   `sarvis` while the English-learned term reduces to `servike`, so homophone terms stay
   guarded in Devanagari even when the sentence does support them. Conservative rather
   than wrong, and pinned by a failing case. Other Indic scripts are not handled at all.
6. ~~`APPLY_THRESHOLD` is nearly redundant.~~ **Fixed.** A branch-coverage table showed
   it was never reached, because `SIM_FLOOR` rejected almost everything first. The floor
   dropped from 0.85 to 0.60, demoting it to a cheap pre-filter and letting the score —
   which accounts for evidence and context, as a raw floor cannot — do the deciding. Same
   results, same adversarial numbers, full branch coverage, and a more truthful trace.
   Kept here rather than deleted so the arc is visible: DISCOVERIES.md §9 diagnosed it,
   §15 acted on it.
7. **The `usage` observation confirms every entry it mentions.** A user quoting someone
   else's text would strengthen memories they did not intend to.
8. **The decision trace keeps only the most recent 200 requests.** It is an inspection
   aid, not an audit log, and unbounded it outgrew the memory it explains — 2.5 MB after
   5,000 utterances against 324 KB now. A product that needed durable history would want
   a real retention policy rather than a ring buffer. See DISCOVERIES.md §14.

## AI use

This repository was built with Claude (Claude Code), used as an implementation partner
rather than an autocomplete. The architecture, product decisions, thresholds and
evaluation design were specified and argued through in conversation before code was
written; Claude wrote most of the code and prose against those decisions, and I directed,
corrected and verified them. Every number in this repository was produced by running the
code, not by an assistant recalling what it expected.

The working method was to make claims checkable rather than to argue them, and most of
what is good here came from a measurement contradicting something we had written down.
`DISCOVERIES.md` is that record — thirteen findings, of which roughly half are corrections
to our own earlier mistakes:

- The **eval-before-implementation commit ordering** was deliberate, so the evaluation's
  independence is verifiable rather than merely asserted.
- Measuring `metaphone("Aaditya") = TTY` against `metaphone("adithya") = AT0Y` killed the
  plan to lean on an off-the-shelf phonetic algorithm before a line of matching code was
  written (§1).
- Running the dataset **found a real bug rather than confirming a story**: the
  already-correct check compared case-insensitively, so `Iitm` looked like it was already
  `IITM`. For an acronym the casing *is* the memory (§4).
- A **branch-coverage table** showed one policy rule was never exercised, and the honest
  answer was that the rule is nearly redundant — reported, not papered over with a
  contrived case (§9).
- The README twice asserted the wrong cause for its own latency. The first time the real
  cost was the decision-trace commit, not memory loading (§10). The second time a
  **scale test found an O(n) bug** — 594 ms at 10k entries — that no single-size
  measurement could have shown (§13).
- Adding cross-script support **immediately introduced a false positive** —
  *"मैं कीवी खा रहा हूँ"* was rewritten — because the homophone guard compared raw strings
  against an English word list (§12).
- The **adversarial harness** is what made the b/v tier safe to build. The fix had been
  described in §7 for several commits and left unimplemented precisely because there was
  no way to know what widening retrieval would cost. Once there was, the evidence was a
  diff: one case artifact changed, false positives stayed at zero (§7).

Two decisions were made against the grain and are argued rather than hidden: **no live
LLM call** in the resolution path, and **keeping failing cases in the dataset**.

Everything here has been run and verified from a clean clone of the submitted commit.

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
| `eval/scale.py` | Latency, retrieval breadth and correctness at 10 → 10,000 entries |
| `eval/check_docs.py` | Asserts every number in this file and RUN.md matches generated results |
| `eval/results/` | Committed generated results |
| `tests/test_phonetics.py` | `python -m tests.test_phonetics`, no pytest needed |
| `tests/test_validation.py` | Input-validation regressions found by a hostile-input sweep |
| `DISCOVERIES.md` | The failure modes found while building, linked to their cases |
