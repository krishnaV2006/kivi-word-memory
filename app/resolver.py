"""From formatted text to memory-aware text, with a reason for every decision.

Retrieval and decision are deliberately separate stages.

Retrieval is allowed to be greedy. Three key algorithms cast a wide net, and the loose
consonant key in particular over-generates on purpose -- "cave" retrieves "Kivi". That
is fine. A key collision is not an intervention.

The decision stage is where the system is conservative. It has to clear four bars: the
entry must be active, the span must not already be correct, the score must clear the
apply threshold, and it must beat its runner-up by a margin. A homophone that is also
an ordinary English word has to clear a fifth: something in the sentence must actually
belong to that memory's world.

Every span the system looks at produces a Decision, including the ones where it does
nothing, because "why did Kivi leave this alone" is as much a product question as
"why did it change this".
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from app import apply as apply_mod
from app.memory import note_application
from app.phonetics import indic_skeleton, keys_for, similarity

# --- policy constants -------------------------------------------------------------
# Small, few, and stated out loud. Tuning these is a product decision, not a detail.

SIM_FLOOR = 0.85        # below this a retrieved candidate is not considered at all
APPLY_THRESHOLD = 0.72  # score a candidate must reach to be applied
MARGIN = 0.06           # how far ahead of the runner-up the winner must be
LOOSE_PENALTY = 0.85    # loose-key-only matches are a weaker route to the same entry
FUZZY_PENALTY = 0.80    # b/v-folded matches are weaker still, and gated on context
CONTEXT_SATURATION = 3.0  # matched context weight at which the boost maxes out
CONTEXT_WEIGHT = 0.10   # how much context can move a score
MAX_NGRAM = 3           # longest multiword identity we will consider

# The decision trace exists so a person can ask "why did Kivi just do that". That is a
# question about recent history: nobody inspects why a word was changed six months ago,
# and Kivi resolves an utterance every time its user speaks. Left unbounded the trace
# grows about 3 rows per utterance forever -- measured at 2.5 MB after 5,000 utterances,
# which is larger than everything the system actually remembers. So it is a ring buffer
# over requests, not a log.
TRACE_RETENTION_REQUESTS = 200


@dataclass
class Candidate:
    entry_id: int
    canonical: str
    kind: str
    status: str
    confidence: float
    similarity: float
    route: str            # 'indic' (exact skeleton), 'metaphone', or 'indic_loose'
    context_boost: float
    score: float


@dataclass
class Decision:
    span: str
    start: int
    end: int
    action: str
    reason: str
    entry_id: int | None = None
    canonical: str | None = None
    score: float | None = None
    runner_up: float | None = None
    candidates: list[dict] = field(default_factory=list)


@dataclass
class ResolveResult:
    request_id: str
    asr: str
    formatted: str
    memory_aware: str
    intervened: bool
    decisions: list[Decision]
    memory_prompt_block: str
    timings_ms: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["decisions"] = [asdict(x) if not isinstance(x, dict) else x for x in self.decisions]
        return d


# --- the memory view ----------------------------------------------------------------

@dataclass
class MemoryView:
    """Everything resolution needs, read from SQLite in one pass.

    Resolution used to issue a query per retrieval key and another per candidate entry,
    which made the database the dominant cost of a request. Loading once is both faster
    and easier to reason about: a decision is now a pure function of this snapshot.

    Callers that resolve many utterances against unchanging memory (the evaluation, the
    adversarial harness) build one view and reuse it. The HTTP layer deliberately does
    NOT: it loads fresh per request, because observations mutate memory between requests
    and a stale-cache bug that silently applies a suppressed entry would be far worse
    than a few milliseconds. See README limitations.
    """
    entries: dict[int, dict]
    surfaces: dict[int, list[str]]
    key_index: dict[tuple[str, str], list[int]]
    ctx_index: dict[int, dict[str, float]]
    common: set[str]
    common_skeletons: set[str]


def load_view(conn: sqlite3.Connection) -> MemoryView:
    entries = {
        r["id"]: {
            "id": r["id"], "canonical": r["canonical"], "kind": r["kind"],
            "status": r["status"], "confidence": r["confidence"],
            "protected": r["protected"],
        }
        for r in conn.execute("SELECT * FROM entries")
    }

    surfaces: dict[int, list[str]] = {}
    for r in conn.execute("SELECT entry_id, surface FROM surfaces"):
        surfaces.setdefault(r["entry_id"], []).append(r["surface"])

    key_index: dict[tuple[str, str], list[int]] = {}
    for r in conn.execute("SELECT entry_id, key, algo FROM phonetic_keys"):
        key_index.setdefault((r["key"], r["algo"]), []).append(r["entry_id"])

    # Context terms are stored as written but matched as skeletons, so a term learned as
    # 'sarvam' still fires when the ASR writes 'sarwam'.
    ctx_index: dict[int, dict[str, float]] = {}
    for r in conn.execute("SELECT entry_id, term, weight FROM context_terms"):
        ctx_index.setdefault(r["entry_id"], {})[indic_skeleton(r["term"])] = r["weight"]

    common = {r["word"] for r in conn.execute("SELECT word FROM common_words")}
    # The guard asks "is this an ordinary word", which is a question about sound, not
    # spelling. Comparing raw strings let a Devanagari token slip past it entirely --
    # कीवी is not in an English word list, so "I am eating a kiwi" written in Hindi was
    # rewritten to the product name. Comparing skeletons closes that.
    common_skeletons = {indic_skeleton(w) for w in common}
    common_skeletons.discard("")

    return MemoryView(entries, surfaces, key_index, ctx_index, common, common_skeletons)


# --- retrieval and scoring ----------------------------------------------------------

def _retrieve(view: MemoryView, span: str) -> dict[int, str]:
    """entry_id -> best retrieval route for this span. 'indic' beats 'metaphone'
    beats 'indic_loose'."""
    rank = {"indic": 0, "metaphone": 1, "indic_loose": 2, "indic_fuzzy": 3}
    hits: dict[int, str] = {}
    for key, algo in keys_for(span):
        for eid in view.key_index.get((key, algo), ()):
            if eid not in hits or rank[algo] < rank[hits[eid]]:
                hits[eid] = algo
    return hits


def _score_candidates(
    view: MemoryView,
    span: str,
    sentence_skeletons: dict[str, int],
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for entry_id, route in _retrieve(view, span).items():
        entry = view.entries.get(entry_id)
        if entry is None:
            continue

        # Similarity against the closest known spelling of this entry. An exact skeleton
        # match is a categorically stronger signal than a fuzzy one, so it scores 1.0.
        if route == "indic":
            sim = 1.0
        else:
            best = max(
                (similarity(span, s) for s in view.surfaces.get(entry_id, [entry["canonical"]])),
                default=0.0,
            )
            penalty = {"indic_loose": LOOSE_PENALTY, "indic_fuzzy": FUZZY_PENALTY}.get(route, 1.0)
            sim = best * penalty
            if best < SIM_FLOOR:
                continue

        matched_weight = sum(
            w for skel, w in view.ctx_index.get(entry_id, {}).items()
            if skel in sentence_skeletons
        )
        boost = min(1.0, matched_weight / CONTEXT_SATURATION)

        # The fuzzy tier folds b/v/w, which is a real ASR confusion but also a real way
        # to collide unrelated words. It is allowed to retrieve freely and then required
        # to earn its place: without independent context support from the sentence, the
        # candidate is dropped before it can compete. See DISCOVERIES.md section 7.
        if route == "indic_fuzzy" and boost <= 0.0:
            continue

        # Evidence modulates similarity rather than adding to it. A well-trusted entry
        # must still sound like the span; confidence is a tiebreaker, not a driver.
        score = min(1.0, sim * (0.75 + 0.25 * entry["confidence"]) + CONTEXT_WEIGHT * boost)

        candidates.append(Candidate(
            entry_id=entry_id,
            canonical=entry["canonical"],
            kind=entry["kind"],
            status=entry["status"],
            confidence=entry["confidence"],
            similarity=round(sim, 4),
            route=route,
            context_boost=round(boost, 4),
            score=round(score, 4),
        ))
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def _decide(
    span_text: str,
    stem: str,
    start: int,
    end: int,
    candidates: list[Candidate],
    common: set[str],
    common_skeletons: set[str],
) -> Decision:
    """The whole policy, in one readable place."""
    payload = [asdict(c) for c in candidates[:4]]

    if not candidates:
        return Decision(span=span_text, start=start, end=end, action="no_candidate",
                        reason="no memory entry shares a phonetic key with this span",
                        candidates=payload)

    # Already correct: applying this memory would not change the text. Note this is
    # asked as "would the rewrite be a no-op", not "do the strings match ignoring case".
    # For an acronym the casing IS the memory -- 'Iitm' is not already 'IITM'.
    for c in candidates:
        would_write = apply_mod.render_replacement(stem, c.canonical, c.kind)
        if would_write == stem and c.status == "active":
            others = [x for x in candidates if x.entry_id != c.entry_id]
            note = ""
            if others:
                note = (f"; nearest competitor '{others[0].canonical}' scored "
                        f"{others[0].score} and was not close enough to displace it")
            return Decision(span=span_text, start=start, end=end, action="noop_already_correct",
                            reason=f"span already matches active memory '{c.canonical}' exactly{note}",
                            entry_id=c.entry_id, canonical=c.canonical, score=c.score,
                            candidates=payload)

    top = candidates[0]
    runner_up = candidates[1].score if len(candidates) > 1 else None

    if top.status == "suppressed":
        return Decision(span=span_text, start=start, end=end, action="abstain_suppressed",
                        reason=(f"'{top.canonical}' was reverted by the user and is suppressed; "
                                f"it stays in memory but will not act"),
                        entry_id=top.entry_id, canonical=top.canonical, score=top.score,
                        runner_up=runner_up, candidates=payload)

    if top.status == "candidate":
        return Decision(span=span_text, start=start, end=end, action="abstain_low_evidence",
                        reason=(f"'{top.canonical}' has been seen once and is still a candidate; "
                                f"two confirmations are required before acting"),
                        entry_id=top.entry_id, canonical=top.canonical, score=top.score,
                        runner_up=runner_up, candidates=payload)

    if top.score < APPLY_THRESHOLD:
        return Decision(span=span_text, start=start, end=end, action="abstain_low_score",
                        reason=(f"closest memory '{top.canonical}' scored {top.score}, "
                                f"below the {APPLY_THRESHOLD} apply threshold"),
                        entry_id=top.entry_id, canonical=top.canonical, score=top.score,
                        runner_up=runner_up, candidates=payload)

    if runner_up is not None and (top.score - runner_up) < MARGIN:
        return Decision(span=span_text, start=start, end=end, action="abstain_ambiguous",
                        reason=(f"'{top.canonical}' ({top.score}) and "
                                f"'{candidates[1].canonical}' ({runner_up}) are within "
                                f"{MARGIN} of each other; refusing to guess between two memories"),
                        entry_id=top.entry_id, canonical=top.canonical, score=top.score,
                        runner_up=runner_up, candidates=payload)

    # The homophone guard. A memory whose sound is also an ordinary English word needs
    # positive evidence from the sentence, not merely the absence of evidence against.
    if (
        stem.lower() in common or indic_skeleton(stem) in common_skeletons
    ) and top.context_boost <= 0.0:
        return Decision(span=span_text, start=start, end=end, action="abstain_common_word",
                        reason=(f"'{stem}' is an ordinary English word and nothing in this "
                                f"sentence belongs to '{top.canonical}'; leaving it alone"),
                        entry_id=top.entry_id, canonical=top.canonical, score=top.score,
                        runner_up=runner_up, candidates=payload)

    return Decision(span=span_text, start=start, end=end, action="apply",
                    reason=(f"'{stem}' matches '{top.canonical}' via {top.route} "
                            f"(similarity {top.similarity}, confidence {top.confidence}, "
                            f"context {top.context_boost}) scoring {top.score}"),
                    entry_id=top.entry_id, canonical=top.canonical, score=top.score,
                    runner_up=runner_up, candidates=payload)


def build_memory_prompt_block(view: MemoryView, sentence: str) -> str:
    """The memory context that would be injected into a formatting prompt.

    We do not need an LLM to produce the memory-aware output -- the deterministic path
    above already does it -- but this is the seam where memory meets a model, so it is
    a first-class output rather than an implementation detail.
    """
    # Look the sentence up in the key index rather than scanning every entry. Scanning
    # was O(number of entries) per request and recomputed a skeleton for every surface
    # in memory, which made this the dominant cost of a resolution long before retrieval
    # was: 594 ms at 10k entries, against 2 candidates actually retrieved. See
    # DISCOVERIES.md section 13.
    hit_ids: set[int] = set()
    for token in apply_mod.tokenize(sentence):
        skeleton = indic_skeleton(token.stem)
        if skeleton:
            hit_ids.update(view.key_index.get((skeleton, "indic"), ()))

    lines: list[str] = []
    for entry_id in sorted(hit_ids, key=lambda i: view.entries[i]["canonical"]
                           if i in view.entries else ""):
        entry = view.entries.get(entry_id)
        if entry is None or entry["status"] != "active":
            continue
        surfaces = view.surfaces.get(entry_id, [])
        variants = sorted({s for s in surfaces if s.lower() != entry["canonical"].lower()})
        hint = f" (heard as: {', '.join(variants)})" if variants else ""
        lines.append(f"- {entry['canonical']} [{entry['kind']}]{hint}")
    if not lines:
        return "# Known personal terms\n(none relevant to this utterance)"
    return "# Known personal terms\n" + "\n".join(lines)


def _prune_trace(conn: sqlite3.Connection) -> None:
    """Keep the trace to the most recent TRACE_RETENTION_REQUESTS requests.

    Pruning whole requests rather than rows, so an inspectable trace is never half
    deleted. The count check keeps this cheap: the O(n) delete runs roughly once every
    TRACE_RETENTION_REQUESTS resolutions rather than on every one.
    """
    n = conn.execute("SELECT COUNT(DISTINCT request_id) AS c FROM decisions").fetchone()["c"]
    if n <= TRACE_RETENTION_REQUESTS * 2:
        return
    conn.execute(
        """
        DELETE FROM decisions WHERE request_id NOT IN (
            SELECT request_id FROM decisions
            GROUP BY request_id ORDER BY MAX(id) DESC LIMIT ?
        )
        """,
        (TRACE_RETENTION_REQUESTS,),
    )


def resolve(
    conn: sqlite3.Connection,
    asr: str,
    formatted: str,
    view: MemoryView | None = None,
    record: bool = True,
) -> ResolveResult:
    """Level 2 (formatted) -> level 3 (memory-aware).

    We rewrite the formatted text, because that is what reaches the user. The ASR text
    is carried through for the record and for the prompt block.

    Pass `view` to reuse a memory snapshot across many calls; leave it None and memory
    is read fresh, which is what the HTTP layer does. Set `record=False` to skip writing
    the decision trace, for bulk runs that would otherwise bloat the database.
    """
    request_id = uuid.uuid4().hex[:12]
    t0 = time.perf_counter()

    text = formatted if (formatted or "").strip() else (asr or "")
    view = view if view is not None else load_view(conn)
    t_load = time.perf_counter()

    ranges = apply_mod.protected_ranges(text)
    tokens = apply_mod.tokenize(text)
    sentence_skeletons = {indic_skeleton(t.stem): 1 for t in tokens}

    # Longest spans first so that a multiword identity wins over its parts.
    spans: list[tuple[int, int, str, str]] = []
    for n in range(MAX_NGRAM, 0, -1):
        for i in range(len(tokens) - n + 1):
            group = tokens[i:i + n]
            start, end = group[0].start, group[-1].end
            stem = " ".join(g.stem for g in group) if n > 1 else group[0].stem
            spans.append((start, end, text[start:end], stem))
    t_spans = time.perf_counter()

    decisions: list[Decision] = []
    edits: list[tuple[int, int, str]] = []
    consumed: list[tuple[int, int]] = []

    for start, end, span_text, stem in spans:
        if any(start < c_end and end > c_start for c_start, c_end in consumed):
            continue
        if apply_mod.overlaps_protected(start, end, ranges):
            if _retrieve(view, stem):
                decisions.append(Decision(
                    span=span_text, start=start, end=end, action="abstain_protected_span",
                    reason="span sits inside an email address, URL, handle or code block",
                ))
                consumed.append((start, end))
            continue

        candidates = _score_candidates(view, stem, sentence_skeletons)
        if not candidates:
            continue

        decision = _decide(span_text, stem, start, end, candidates,
                           view.common, view.common_skeletons)
        decisions.append(decision)
        consumed.append((start, end))

        if decision.action == "apply" and decision.canonical:
            top = candidates[0]
            suffix = span_text[len(stem):] if span_text.startswith(stem) else ""
            replacement = apply_mod.render_replacement(stem, decision.canonical, top.kind) + suffix
            edits.append((start, end, replacement))
            if record:
                note_application(conn, top.entry_id)

    t_decide = time.perf_counter()
    memory_aware = apply_mod.apply_edits(text, edits)
    prompt_block = build_memory_prompt_block(view, text)
    t_end = time.perf_counter()

    if record:
        for d in decisions:
            conn.execute(
                """INSERT INTO decisions (request_id, span, entry_id, action, reason, score,
                                          runner_up, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (request_id, d.span, d.entry_id, d.action, d.reason, d.score, d.runner_up),
            )
        _prune_trace(conn)
        conn.commit()

    ms = lambda a, b: round((b - a) * 1000, 4)  # noqa: E731
    return ResolveResult(
        request_id=request_id,
        asr=asr,
        formatted=formatted,
        memory_aware=memory_aware,
        intervened=bool(edits),
        decisions=decisions,
        memory_prompt_block=prompt_block,
        timings_ms={
            "load_memory": ms(t0, t_load),
            "span_generation": ms(t_load, t_spans),
            "scoring_and_decision": ms(t_spans, t_decide),
            "rewrite_and_prompt": ms(t_decide, t_end),
            "total": ms(t0, t_end),
        },
    )
