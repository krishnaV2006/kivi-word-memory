"""Adversarial false-positive harness. Run: python -m eval.adversarial

A curated dataset proves the cases its author thought of. This proves the opposite
property at a scale no one can hand-curate: across thousands of sentences that contain
nothing the user has ever taught Kivi, memory must change **nothing at all**.

That is the property most likely to be quietly false. The loose consonant key is
deliberately promiscuous -- `cave` retrieves `Kivi` -- so every ordinary sentence is an
opportunity for a spurious rewrite, and a rewrite the user did not ask for is the
expensive failure this whole system is built to avoid.

Five corpora. Four are scored for false positives, one is a control:

  neutral        ordinary sentences assembled from everyday vocabulary
  shapes         paragraphs, markdown, all-caps, quotes, numbers, degenerate input
  homophone      each memory's ordinary-English twin in clearly non-memory contexts
  devanagari     Hindi in native script, including the fruit कीवी, which must stay a fruit
  names          real personal names that are NOT this user's, in natural frames
  names_control  names that ARE the user's person under another spelling -- these are
                 EXPECTED to be rewritten

The names corpus is the sharp one: two hundred real names, most of them Indian and so
sitting in the exact phonetic neighbourhood the skeleton rules were tuned for. Any
rewrite there renames a real person.

The control group exists because a test that only ever proves a negative is worthless if
the pipeline is silently inert. `Aditya` is the same person as `Aaditya`, so those
sentences must be rewritten, and if they are not, this harness has no power to detect a
false positive either. It is split out automatically by comparing strict phonetic
skeletons -- names that merely share a loose consonant key stay in the scored corpus,
because those near-misses are the whole point.

Deterministic: fixed seed, so the reviewer gets the numbers in the committed report.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "eval" / "results"
ADV_DB = REPO_ROOT / "eval" / ".adversarial.db"

os.environ.setdefault("KIVI_DB_PATH", str(ADV_DB))

from app import db as db_mod                      # noqa: E402
from app.phonetics import indic_skeleton          # noqa: E402
from app.resolver import load_view, resolve       # noqa: E402
from seed.seed import seed as run_seed            # noqa: E402

SEED = 20260907

# Real personal names, weighted towards the Indian naming space the phonetic rules were
# built for, because that is where a spurious match is most likely. None of these belong
# to the seeded persona, so any rewrite of one is a false positive.
NAMES = """
Rahul Priya Arjun Sneha Vikram Ananya Rohit Kavya Suresh Meera Anil Divya Karthik Pooja
Manish Shreya Rajesh Nisha Sandeep Anjali Vivek Ritu Ashok Swati Naveen Deepa Gaurav
Neha Harish Lakshmi Imran Farah Zoya Kabir Aryan Ishaan Advait Reyansh Vihaan Atharv
Shaurya Dhruv Kiaan Ayaan Rudra Arnav Aarav Vedant Yash Parth Nikhil Siddharth Abhishek
Akash Amit Ankit Bhavesh Chirag Darshan Devendra Gopal Hemant Jatin Kunal Lalit Mohit
Nitin Omkar Pankaj Rakesh Sachin Tarun Umesh Varun Yogesh Zubin Aditya Bhavna Chitra
Damini Ekta Falguni Geeta Hema Indira Jyoti Kiran Latha Madhuri Nandita Ojas Padma
Rekha Sarika Tanvi Usha Vandana Yamini Zeenat Aarti Bindu Charu Deepti Esha Gauri
Harini Ira Janhavi Kamala Leela Malini Nalini Oviya Pallavi Radha Sumitra Trisha Uma
Vaishali Anushka Bhoomi Chaitanya Devika Gitanjali Harshita Ishita Kalpana Manjula
Nivedita Poornima Rashmi Shalini Tejaswini Vidya Yashoda Abhay Balaji Chandran Dinesh
Ganesh Harsha Jagdish Krishnan Mahesh Narayan Prakash Raghav Shankar Venkat Anand
Bharat Chetan Dilip Girish Jayant Kailash Mukesh Nirmal Pramod Ravi Sanjay Tushar
Vinod Alok Basant Charan Dhruva Gagan Jaideep Keshav Mandar Nakul Pranav Rishi Samir
Tanmay Vikas Aakash James Sarah Michael Emma David Laura Thomas Anna Peter Claire
Daniel Julia Robert Maria Stephen Helen William Grace Oliver Sophie Henry Alice
""".split()

# Ordinary vocabulary, chosen to contain no memory terms.
SUBJECTS = ["the team", "my manager", "the client", "our designer", "the reviewer",
            "everyone", "the group", "my roommate", "the committee", "the students"]
VERBS = ["finished", "postponed", "approved", "cancelled", "discussed", "rewrote",
         "shipped", "tested", "measured", "documented", "questioned", "delayed"]
OBJECTS = ["the report", "the schedule", "the budget", "the migration", "the proposal",
           "the release notes", "the onboarding flow", "the invoice", "the roadmap",
           "the deployment", "the contract", "the survey results"]
TAILS = ["yesterday afternoon", "before the deadline", "without any warning",
         "after a long discussion", "over the weekend", "in the morning meeting",
         "twice this month", "at the last minute", "for the third time", "today"]

NAME_FRAMES = [
    "Ask {n} to review the pull request.",
    "{n} joined the standup this morning.",
    "I spoke to {n} about the timeline yesterday.",
    "Please forward the notes to {n}.",
    "{n} said the schedule looks fine.",
]

# Each memory's ordinary-English twin in contexts that plainly are not about the product.
HOMOPHONE_FRAMES = {
    "kiwi": [
        "I ate a kiwi for breakfast.",
        "The kiwi is a bird that cannot fly.",
        "She packed an apple and a kiwi.",
        "Kiwi fruit grows well in this climate.",
        "He bought a kiwi from the market.",
        "New Zealand exports a lot of kiwi.",
        "Add kiwi to the fruit salad please.",
        "The kiwi was too sour to eat.",
    ],
    "cave": [
        "We explored a cave near the coast.",
        "The cave was cold and completely dark.",
        "Bats live inside that cave.",
        "They found paintings on the cave wall.",
    ],
    "sarah": [
        "Sarah from marketing sent the deck.",
        "I met Sarah at the conference.",
        "Sarah will chair the meeting.",
    ],
    "service": [
        "The service is down again.",
        "Customer service called me back.",
        "That restaurant has terrible service.",
    ],
}


# Devanagari, because transliteration opened a whole second script to matching and an
# untested script is an untested attack surface. Half of these contain the fruit कीवी,
# which is a homophone of the product and must stay a fruit; the rest are ordinary Hindi
# containing names and words memory has never been taught.
DEVANAGARI_SENTENCES = [
    "मैं कीवी खा रहा हूँ।",
    "बाज़ार से कीवी ले आना।",
    "कीवी एक फल है।",
    "उसने नाश्ते में कीवी खाया।",
    "कीवी बहुत महंगा है।",
    "राहुल को बोल दो कि मैं आ रहा हूँ।",
    "कल बैठक सुबह दस बजे है।",
    "मुझे यह रिपोर्ट कल तक चाहिए।",
    "प्रिया ने दस्तावेज़ भेज दिए हैं।",
    "टीम ने काम पूरा कर लिया।",
    "यह किताब बहुत अच्छी है।",
    "हमें बजट पर चर्चा करनी है।",
    "विकास ने अनुबंध पढ़ा।",
    "गुफा के अंदर बहुत अंधेरा था।",
    "सेवा फिर से बंद हो गई है।",
    "मीरा कल दफ़्तर नहीं आई।",
    "अनिल ने प्रस्ताव मंज़ूर किया।",
    "शाम को बारिश होने वाली है।",
]


# Shapes of text the other corpora do not produce. Everything above is short and
# well-formed; real dictation is not. None of these contains a memory term, so none may
# be touched -- the point is that structure, casing and punctuation do not create matches.
SHAPE_SENTENCES = [
    # a paragraph rather than a sentence
    "The team finished the migration yesterday. Everyone agreed the schedule was tight, "
    "but the client approved the budget and the deployment went out on time. We should "
    "review the invoice before the deadline.",
    # markdown structure, a code span and a link
    "## Notes\n- reviewed the **budget**\n- `npm run build` failed\n"
    "- see [docs](https://example.com/guide)",
    "THE SERVICE IS DOWN AGAIN AND NOBODY KNOWS WHY",
    "theteamfinishedthemigrationyesterday",
    "We shipped 3 builds, 42 tests passed, 99.9% uptime, v2.1.0 released.",
    'She said "the service is down" and left.',
    "a well-known service-level agreement was re-reviewed",
    "The teams' reports were filed.",
    "   ",
    "a",
]


def build_corpora() -> dict[str, list[str]]:
    rng = random.Random(SEED)

    neutral = []
    for _ in range(600):
        neutral.append(
            f"{rng.choice(SUBJECTS).capitalize()} {rng.choice(VERBS)} "
            f"{rng.choice(OBJECTS)} {rng.choice(TAILS)}."
        )

    homophone = [s for group in HOMOPHONE_FRAMES.values() for s in group]

    return {
        "neutral": sorted(set(neutral)),
        "homophone": homophone,
        "devanagari": list(DEVANAGARI_SENTENCES),
        "shapes": list(SHAPE_SENTENCES),
    }


def split_names(known: set[str]) -> tuple[list[str], list[str], list[str]]:
    """Partition the name list into strangers and the user's own person.

    Returns (stranger_sentences, control_sentences, control_names).
    """
    strangers, controls, control_names = [], [], []
    for name in NAMES:
        target = controls if indic_skeleton(name) in known else strangers
        if indic_skeleton(name) in known:
            control_names.append(name)
        for frame in NAME_FRAMES:
            target.append(frame.format(n=name))
    return strangers, controls, control_names


def memory_terms(conn) -> set[str]:
    """Strict skeletons of everything memory knows.

    Used to split the names corpus. A name whose strict skeleton equals a memory term's
    is not a stranger -- it IS the user's person under a different spelling, and
    rewriting it is correct. Those names become the control group. Names that merely
    share a loose consonant key stay in the false-positive corpus, because those are
    precisely the near-misses worth stressing.
    """
    out = set()
    for r in conn.execute("SELECT canonical FROM entries"):
        out.add(indic_skeleton(r["canonical"]))
    for r in conn.execute("SELECT surface FROM surfaces"):
        out.add(indic_skeleton(r["surface"]))
    return out


def run() -> dict:
    for suffix in ("", "-wal", "-shm"):
        p = ADV_DB.with_name(ADV_DB.name + suffix)
        if p.exists():
            p.unlink()
    db_mod.migrate(verbose=False)
    conn = db_mod.connect()
    run_seed(conn, verbose=False)

    known = memory_terms(conn)
    view = load_view(conn)
    corpora = build_corpora()
    strangers, controls, control_names = split_names(known)
    corpora["names"] = strangers
    corpora["names_control"] = controls

    results: dict[str, dict] = {}
    all_hits: list[dict] = []
    t0 = time.perf_counter()
    total = 0

    for corpus, sentences in corpora.items():
        hits = []
        for sentence in sentences:
            total += 1
            # record=False: this is a measurement, not usage. Writing ~1900 decision
            # traces would inflate the database growth numbers reported elsewhere.
            r = resolve(conn, sentence, sentence, view=view, record=False)
            if r.intervened:
                skels = {indic_skeleton(t) for t in sentence.split()}
                hits.append({
                    "corpus": corpus,
                    "input": sentence,
                    "output": r.memory_aware,
                    "reason": next((d.reason for d in r.decisions if d.action == "apply"), ""),
                    "overlaps_known_term": bool(skels & known),
                })
        results[corpus] = {
            "sentences": len(sentences),
            "interventions": len(hits),
            "rate": round(len(hits) / len(sentences), 6) if sentences else 0.0,
            "expects_interventions": corpus == "names_control",
        }
        if corpus != "names_control":
            all_hits.extend(hits)

    elapsed = time.perf_counter() - t0
    conn.close()
    for suffix in ("", "-wal", "-shm"):
        p = ADV_DB.with_name(ADV_DB.name + suffix)
        if p.exists():
            p.unlink()

    scored = sum(r["sentences"] for name, r in results.items() if name != "names_control")
    return {
        "seed": SEED,
        "total_sentences": scored,
        "all_sentences_including_control": total,
        "total_interventions": len(all_hits),
        "false_positive_rate": round(len(all_hits) / scored, 6) if scored else 0.0,
        "elapsed_s": round(elapsed, 2),
        "per_sentence_ms": round(elapsed * 1000 / total, 4) if total else 0.0,
        "distinct_names_tested": len(NAMES),
        "control_names": control_names,
        "control_recall": round(
            results["names_control"]["interventions"] / results["names_control"]["sentences"], 4
        ) if results.get("names_control", {}).get("sentences") else None,
        "by_corpus": results,
        "interventions": all_hits,
    }


def write_report(payload: dict) -> None:
    lines: list[str] = []
    w = lines.append
    w("# Adversarial false-positive results\n")
    w(f"Generated by `python -m eval.adversarial` (seed `{payload['seed']}`, deterministic).\n")
    w(f"**{payload['total_sentences']} sentences containing nothing the user has taught "
      f"Kivi. {payload['total_interventions']} interventions.**\n")
    if payload.get("control_recall") is not None:
        ctl = payload["by_corpus"]["names_control"]
        w(f"Control group: {ctl['interventions']} of {ctl['sentences']} sentences rewritten "
          f"(recall {payload['control_recall']}). The control is what makes the zero above "
          f"mean something — it proves the pipeline does fire when it should, so a clean "
          f"scored result is not merely an inert system.\n")
    w("A curated dataset proves the cases its author imagined. This proves the property "
      "most likely to be quietly false: that memory stays out of the way of ordinary text. "
      "The loose consonant key retrieves candidates constantly here — `cave` pulls up "
      "`Kivi` in every sentence that contains it — so this measures whether the decision "
      "stage actually holds.\n")
    w("| corpus | sentences | interventions | rate |")
    w("|---|---:|---:|---:|")
    for name, r in payload["by_corpus"].items():
        w(f"| {name} | {r['sentences']} | {r['interventions']} | {r['rate']} |")
    w(f"| **total** | **{payload['total_sentences']}** | "
      f"**{payload['total_interventions']}** | **{payload['false_positive_rate']}** |")
    w("\n## What each corpus attacks\n")
    w("- **neutral** — ordinary workplace sentences assembled from everyday vocabulary "
      "containing no memory term.\n"
      f"- **names** — {payload['distinct_names_tested']} real personal names that are not "
      "this user's, mostly Indian and therefore sitting in the exact phonetic "
      "neighbourhood the skeleton rules were built for, across five sentence frames. Any "
      "rewrite here renames a real person.\n"
      "- **homophone** — each memory's ordinary-English twin (`kiwi` the fruit, `cave`, "
      "`Sarah`, `service`) in contexts that are plainly not about the product.\n"
      "- **names_control** *(not scored for false positives)* — names that are the user's "
      "own person under a different spelling. These must be rewritten, and the split is "
      "made automatically on strict phonetic skeleton rather than by hand.\n")

    hits = payload["interventions"]
    w(f"\n## Interventions ({len(hits)})\n")
    if not hits:
        w("None. No sentence in any corpus was modified.\n")
    else:
        w("| corpus | input | output | shares a key with a known term |")
        w("|---|---|---|:-:|")
        for h in hits[:60]:
            w(f"| {h['corpus']} | `{h['input']}` | `{h['output']}` | "
              f"{'yes' if h['overlaps_known_term'] else 'no'} |")
        if len(hits) > 60:
            w(f"\n…and {len(hits) - 60} more; see `adversarial.json`.\n")
        w("\nEach of these is a false positive unless the flagged span genuinely is the "
          "user's own term reappearing. Reasons are in `adversarial.json`.\n")

    w(f"\n## Cost\n")
    # Deliberately no wall-clock number here: this report is meant to be byte-identical
    # across runs so a reviewer can diff it against the committed copy. Timing lives in
    # adversarial.json and in the latency section of summary.md, which are the places
    # that are supposed to move.
    w(f"{payload['all_sentences_including_control']} resolutions, reusing one "
      f"`MemoryView`. Timing is recorded in `adversarial.json` rather than here, so that "
      f"this report stays byte-identical across runs and can be diffed against the "
      f"committed copy. "
      f"0 model calls, Rs 0.00. Decision traces are not persisted during this run, so it "
      f"does not distort the database-growth figures reported in `summary.md`.\n")

    (RESULTS_DIR / "adversarial.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    print("building corpora ...")
    payload = run()
    (RESULTS_DIR / "adversarial.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_report(payload)
    print(f"{payload['total_sentences']} sentences, "
          f"{payload['total_interventions']} interventions "
          f"(rate {payload['false_positive_rate']})")
    for name, r in payload["by_corpus"].items():
        print(f"  {name:10} {r['interventions']:>3} / {r['sentences']}")
    if payload["interventions"]:
        print("\nFLAGGED:")
        for h in payload["interventions"][:15]:
            print(f"  [{h['corpus']}] {h['input']}")
            print(f"      -> {h['output']}")
    print("\nwritten to eval/results/adversarial.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
