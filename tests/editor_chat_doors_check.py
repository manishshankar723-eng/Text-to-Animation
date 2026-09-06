"""editor_chat_doors_check.py — THE CHAT CAN OFFER PAID WORK, AND IT CAN ASK TO SEE.

    python tests/editor_chat_doors_check.py   (needs node; no backend, no model, no dollar)

Two features, one file, because they are the same shape: a turn that is neither
an answer nor an edit, and whose whole safety property is what it CANNOT do.

    "Chat ke andar price wala button abhi nahi hai … isko karo abhi"
    "Chat tasveer dekh nahi sakti … isko v karo"

**1 · PAID WORK IS AN OFFER — `passes`.** A turn may name up to two of the four
priced doors, and the panel draws a real button for each. ⚠ **THE BUTTON OPENS
THE DOOR; IT DOES NOT SPEND AND IT DOES NOT SAY A PRICE.** Pricing and
entitlement live in exactly one place in this app — the confirm dialog that
✨ Animate, 🎙 Voiceover and 🖼 Animatic images already go through — and a figure
printed in the chat, computed from the board the browser is holding, would be a
second answer about somebody's money sitting next to the one that charges. So
this file asserts the ABSENCE of a price as hard as it asserts the button.

**2 · SEEING IS A REQUEST — `look`.** The chat was blind: it has labels,
durations and dialogue, so *"konsa part bekar hai"* could never be answered. Now
a turn may come back as `kind: "look"` naming the shots it needs, and the browser
re-posts the SAME message with those pictures attached.

⚠ **ONE LOOK PER MESSAGE, ENFORCED IN CODE AND NOT ONLY IN THE PROMPT.** Two
models in a row each asking for a slightly different set of stills is a loop that
spends money on every lap, so `_read_turn(blind=False)` — which is what the call
carrying pictures uses — refuses a look outright.

⚠ **AND THE RULES TRAVEL AS CONTENT ON A LOOK, WHICH IS NOT A STYLE CHOICE.**
Measured against the real API: the full ~9.8KB system instruction with five
stills attached took **149 seconds and then failed**, twice, reproducibly; the
same pictures, prompt and response schema under a two-line system instruction
answered in **7.5 seconds**. Bisected — images alone are fast, the big schema
with images is fast, a short system with images is fast, and only the long system
*together with* image parts hangs. So on a look the rules move into the prompt
and `LOOK_SYSTEM` takes their place. Nothing is dropped: a look obeying a shorter
rulebook would plan worse than a turn that could not see, and nobody would notice
until they read two transcripts side by side.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "client/src/animatic/agent"

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  [{detail}]"))
    if not good:
        failures.append(label)


HARNESS = r"""
import { PAID_DOORS, DOOR_LABEL, MAX_LOOK_SHOTS, normaliseLook, normalisePasses, normaliseTurn }
  from "__TURN__";
import { capabilities } from "__CAPS__";

const caps = capabilities();
const ctx = {
  frames: Array.from({ length: 5 }, (_, i) => ({ id: `f${i}`, duration_ms: 4000 })),
  starts: [0, 4000, 8000, 12000, 16000],
  totalMs: 20000,
  texts: [], shapes: [], transitions: [], overlays: [], audioTracks: [], caps,
};

console.log(JSON.stringify({
  doors: PAID_DOORS,
  labels: DOOR_LABEL,
  maxLook: MAX_LOOK_SHOTS,

  // An unknown door is dropped, not guessed at.
  passes: normalisePasses([
    { door: "veo", shot: 3, why: "shot 3 is a still" },
    { door: "music", why: "not a door" },
    { door: "voiceover" },
  ], ctx),
  // A shot that is not there loses the shot number, not the offer.
  passesOutOfRange: normalisePasses([{ door: "veo", shot: 61 }], ctx),
  // Two is the ceiling, and a repeat is one offer.
  passesCapped: normalisePasses(
    [{ door: "veo" }, { door: "voiceover" }, { door: "images" }], ctx),
  passesDeduped: normalisePasses([{ door: "veo" }, { door: "veo" }], ctx),
  passesJunk: normalisePasses("nonsense", ctx),

  look: normaliseLook({ shots: [4, 2, 2, 99], why: "need to see them" }, ctx),
  lookEmpty: normaliseLook({ shots: [40, 52] }, ctx),
  lookJunk: normaliseLook(null, ctx),

  // ⚠ A LOOK WINS OVER A PLAN ON THE SAME REPLY — the model hedged, and the
  // plan it wrote blind is the one it was unsure enough about to ask about.
  turnLookBeatsPlan: normaliseTurn({
    kind: "plan",
    reply: "let me see",
    look: { shots: [1, 2] },
    plan: { steps: [{ verb: "note", args: {} }] },
  }, caps, ctx).turn,

  // An offer rides on ANY kind of turn, including a plain answer.
  turnAnswerWithOffer: normaliseTurn({
    kind: "answer",
    reply: "a voiceover would carry these",
    passes: [{ door: "voiceover", why: "two silent shots" }],
  }, caps, ctx).turn,

  // …and on a plan, beside the free half of the work.
  // ⚠ A REAL, LANDABLE STEP. `note` takes a `text` and is dropped without one,
  // which turns the whole reply into an `answer` — a fixture that used it would
  // be asserting the wrong thing and passing for the wrong reason.
  turnPlanWithOffer: normaliseTurn({
    kind: "plan",
    reply: "dissolves now, footage if you want it",
    plan: { steps: [{ verb: "add_transition", args: { cut: 1, kind: "dissolve" } }] },
    passes: [{ door: "veo", shot: 2 }],
  }, caps, ctx).turn,
}));
"""


def run_node():
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="chat_doors_")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(
                HARNESS.replace("__TURN__", (AGENT / "chat_turn.js").as_uri())
                .replace("__CAPS__", (AGENT / "capabilities.js").as_uri())
            )
        proc = subprocess.run(["node", harness], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:1200])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    import editor_chat_agent as agent
    import llm_json

    data = run_node()
    if data is None:
        print("  node is not on PATH, or the agent modules would not load.")
        failures.append("the node half could not run")
        data = {}

    # ------------------------------------------------------------------- 1
    # Counted, not written out: a fifth door must not leave this heading lying.
    print(f"\n1 · THE {len(agent.PAID_DOORS)} DOORS, SPELLED THE SAME ON BOTH SIDES\n")
    if data:
        # ⚠ THE MIRROR IS THE POINT. A door renamed on one side and not the other
        # is an offer the panel draws with no name on it, or one the server
        # silently drops — the same arrangement `ASK_REASONS` has.
        check("the client and the server agree on the door names",
              list(data["doors"]) == list(agent.PAID_DOORS),
              f'{data["doors"]} vs {list(agent.PAID_DOORS)}')
        check("…and every door has a label and a glyph to draw",
              all(data["labels"].get(d, {}).get("label") and data["labels"][d].get("glyph")
                  for d in data["doors"]),
              json.dumps(data["labels"]))
        check("…and they agree on how many stills a look may carry",
              data["maxLook"] == agent.MAX_LOOK_SHOTS,
              f'{data["maxLook"]} vs {agent.MAX_LOOK_SHOTS}')

    # ⚠ NO PRICE ANYWHERE NEAR THIS FEATURE. Asserted on the source, because the
    # bug it prevents is somebody helpfully adding one later.
    turn_src = (AGENT / "chat_turn.js").read_text(encoding="utf-8")
    panel_src = (ROOT / "client/src/components/EditorChat.jsx").read_text(encoding="utf-8")
    agent_src = (ROOT / "editor_chat_agent.py").read_text(encoding="utf-8")
    # ⚠ ASSERTED ON THE SOURCE, because the bug this prevents is somebody
    # helpfully adding a price later — and the comment is the only thing that
    # will be in front of them when they do.
    for name, text, needle in (
        ("the reader", turn_src, "two answers about somebody's money"),
        ("the panel", panel_src, "second answer about somebody's money"),
        ("the server", agent_src, "second opinion about money"),
    ):
        check(f"⚠ {name} says outright that no price may be computed here",
              needle in text, f"{name}: {needle!r} not found")
    # ⚠ EVERY DOOR MUST ACTUALLY LEAD SOMEWHERE. A name in `PAID_DOORS` that
    # `openPaidDoor` has no branch for is a button that visibly does nothing,
    # which is the single worst thing a button beside a price can do.
    editor_src = (ROOT / "client/src/components/AnimaticEditor.jsx").read_text(encoding="utf-8")
    opener = editor_src[editor_src.index("function openPaidDoor"):]
    # ⚠ A FIXED WINDOW, NOT A SEARCH FOR THE CLOSING BRACE. Slicing on the first
    # `\n  }\n` cut the function off at its own comment block, so `veo` looked
    # missing and this file reported a bug that was not there — a red line
    # pointing at the wrong thing, which is worse than no line (RULEBOOK G14).
    opener = opener[:opener.index("// ------------------------------------------------------------- exporting")]
    # ⚠ THE COMMENTS COME OUT FIRST, AND FORGETTING THAT COST TWO RED LINES ON
    # CORRECT CODE. The function's own ⚠ note quotes `setSpeechFor("voiceover")`
    # as the thing NOT to do, so a plain substring search found it and reported
    # the very bug that comment exists to prevent. Assert on the CODE.
    code = "\n".join(
        line for line in opener.splitlines() if not line.strip().startswith("//")
    )
    for door in agent.PAID_DOORS:
        # ⚠ THE NAME, NOT `door === "x"`. The last branch is written as an early
        # `door !== "veo"` return — correct, and called missing by a search for
        # the equality form.
        check(f"…and `{door}` has a branch in openPaidDoor",
              f'"{door}"' in code, code[:200])
    # ⚠ THE SAME FUNCTION THE HAND-DRIVEN BUTTON CALLS, NOT THE STATE SETTER.
    # `setSpeechFor("voiceover")` opens the panel with none of its setup done —
    # no flush, no dialogue sheet fetched, last run's confirm and error still
    # there — so the user would read an empty script under a stale price. Found
    # by writing this check, not by seeing it on screen.
    check("⚠ the voiceover door calls openVoiceover(), not setSpeechFor",
          "openVoiceover();" in code and 'setSpeechFor("voiceover")' not in code,
          code[:300])
    check("…and the captions door calls openCaptions()", "openCaptions();" in code)
    # Captions are written FROM a recording, so with no audio there is nothing
    # to listen to and the panel would open with its confirm silently disabled.
    check("…and says why when there is no audio to caption",
          "Add an audio track first" in code, code[:300])
    # ⚠ AND THE VEO DOOR KEEPS BOTH WAYS IN — one named shot, or the whole film.
    check("…and the veo door handles one shot AND the whole film",
          "setAnimateFor(" in code and "openDirector();" in code, code[:400])

    check("the panel's button says the price comes next, on screen",
          "See the price" in panel_src and "Nothing is charged until" in panel_src)
    check("…and it is not disabled while a plan is running",
          "separate doors" in panel_src, "no note about why it stays enabled")

    # ------------------------------------------------------------------- 2
    print("\n2 · AN OFFER IS READ THE SAME WAY ON BOTH SIDES\n")
    py_passes = agent._coerce_passes([
        {"door": "veo", "shot": 3, "why": "shot 3 is a still"},
        {"door": "music", "why": "not a door"},
        {"door": "voiceover"},
    ], 5)
    if data:
        check("an unknown door is dropped, not guessed at",
              [p["door"] for p in data["passes"]] == [p["door"] for p in py_passes]
              == ["veo", "voiceover"],
              f'{data["passes"]} vs {py_passes}')
        check("…a shot number out of range loses the SHOT, not the offer",
              data["passesOutOfRange"] == [{"door": "veo", "why": ""}]
              and agent._coerce_passes([{"door": "veo", "shot": 61}], 5)
              == [{"door": "veo", "why": ""}],
              json.dumps(data["passesOutOfRange"]))
        check("…two offers is the ceiling on both sides",
              len(data["passesCapped"]) == 2
              == len(agent._coerce_passes(
                  [{"door": "veo"}, {"door": "voiceover"}, {"door": "images"}], 5)))
        check("…the same door twice is one offer",
              len(data["passesDeduped"]) == 1
              == len(agent._coerce_passes([{"door": "veo"}, {"door": "veo"}], 5)))
        check("…and junk is no offer at all",
              data["passesJunk"] == [] and agent._coerce_passes("nonsense", 5) == [])
    # ⚠ ONLY `veo` MAY CARRY A SHOT. The other two doors are whole-film, and a
    # stray shot on one would have the panel offering to render through a button
    # that renders nothing.
    check("only the veo door keeps a shot number",
          agent._coerce_passes([{"door": "voiceover", "shot": 2}], 5) == [
              {"door": "voiceover", "why": ""}],
          json.dumps(agent._coerce_passes([{"door": "voiceover", "shot": 2}], 5)))

    # ------------------------------------------------------------------- 3
    print("\n3 · AN OFFER IS NOT AN EDIT, AND NEVER DRAWS AN APPLY BUTTON\n")
    # ⚠ `asked_for_it` IS ON THE FIXTURE BECAUSE THE SCHEMA REQUIRES IT (§3b):
    # this is the case where they DID ask, which is the one that keeps its door.
    only_offer = agent._read_turn(
        {"kind": "plan", "reply": "a voiceover would help",
         "passes": [{"door": "voiceover", "why": "two silent shots",
                     "asked_for_it": True}]},
        {"verbs": [{"id": "note", "args": []}]}, 5)
    # ⚠ THERE IS NOTHING TO APPLY. The spend happens behind a door the chat
    # cannot open, so a turn that only offers is an ANSWER carrying a button.
    check("a turn carrying only an offer is an answer", only_offer["kind"] == "answer",
          only_offer["kind"])
    check("…with no plan on it", only_offer["plan"] is None)
    check("…and the offer still travels", len(only_offer["passes"]) == 1)
    if data:
        check("the client agrees — an answer may carry an offer",
              data["turnAnswerWithOffer"]["kind"] == "answer"
              and len(data["turnAnswerWithOffer"]["passes"]) == 1,
              json.dumps(data["turnAnswerWithOffer"]))
        check("…and a plan may carry one beside the free work",
              data["turnPlanWithOffer"]["kind"] == "plan"
              and len(data["turnPlanWithOffer"]["passes"]) == 1,
              json.dumps(data["turnPlanWithOffer"]))
    # An empty reply is normally an error; an offer is something to show.
    NO_VERBS = {"verbs": [{"id": "note", "args": []}]}
    check("an offer with no words is still a turn, not an error",
          agent._read_turn({"passes": [{"door": "veo", "asked_for_it": True}]},
                           NO_VERBS, 5)["passes"])

    # -----------------------------------------------------------------------
    # ⚠ AN OFFER MAY NOT STAND IN FOR WORK THIS EDITOR COULD HAVE DONE.
    # -----------------------------------------------------------------------
    # Live 2026-09-06, with a screenshot: *"add caption in my story and text on
    # screen"* on a 14-shot reel answered *"I'll add beautiful on-screen text
    # titles across key moments of your story"* — with NOT ONE STEP and a
    # Voiceover button under it. `add_text` is free and was in the vocabulary.
    # *"user caption manga hai to voiceover kyun karne ke liye bol raha hai."*
    bare = agent._read_turn(
        {"kind": "answer", "reply": "I'll add beautiful on-screen text titles.",
         "passes": [{"door": "voiceover", "asked_for_it": False, "why": "it would read it"}]},
        NO_VERBS, 5, asked_text="add caption in my story and text on screen")
    check("⚠ AN OFFER-ONLY TURN LOSES A DOOR THEY DID NOT ASK FOR",
          bare["passes"] == [], str(bare["passes"]))
    check("…and the drop is SAID, not swallowed",
          any(d["verb"] == "voiceover" for d in bare["dropped"]), str(bare["dropped"]))

    # ⚠ THE MODEL'S OWN `asked_for_it` IS NOT THE ONLY TEST, because a model
    # that MISREAD the request reports its misreading honestly — which is exactly
    # how the live one happened. A voiceover adds a VOICE; nobody gets one unless
    # they asked for one somewhere in their own words.
    lied = agent._read_turn(
        {"kind": "answer", "reply": "Shall I read it aloud?",
         "passes": [{"door": "voiceover", "asked_for_it": True}]},
        NO_VERBS, 5, asked_text="add caption in my story and text on screen")
    check("⚠ …AND `asked_for_it: true` DOES NOT SAVE A VOICE NOBODY ASKED FOR",
          lied["passes"] == [], str(lied["passes"]))
    for said in ("voiceover chahiye", "read it out loud please", "isko awaaz do",
                 "add narration"):
        got = agent._read_turn(
            {"kind": "answer", "reply": "Here is what it would do.",
             "passes": [{"door": "voiceover", "asked_for_it": True}]},
            NO_VERBS, 5, asked_text=said)
        check("…but somebody who DID ask keeps their door (%r)" % said,
              len(got["passes"]) == 1, str(got["passes"]))

    # ⚠ AND AN OFFER BESIDE REAL WORK IS A SUGGESTION, WHICH IS ALLOWED. The
    # rule is about an offer standing INSTEAD of an edit, never beside one.
    beside = agent._read_turn(
        {"kind": "plan", "reply": "Titles on four shots.",
         "plan": {"summary": "titles", "steps": [
             {"verb": "add_text", "args": {"shot": 1, "text": "Ganesh Utsav"}}]},
         "passes": [{"door": "voiceover", "asked_for_it": False}]},
        {"verbs": [{"id": "add_text", "args": ["shot", "text"]}]}, 5,
        asked_text="add caption in my story and text on screen")
    check("⚠ AN OFFER BESIDE A REAL PLAN IS KEPT — that is a suggestion, not a sale",
          beside["kind"] == "plan" and len(beside["passes"]) == 1, str(beside))

    # ⚠ AND `captions` CANNOT BE OFFERED OVER A SILENT TIMELINE — that door
    # reads audio ALREADY on the timeline, so the button would price work that
    # cannot run. This one needs no judgement at all: the board counts the tracks.
    silent = agent._read_turn(
        {"kind": "answer", "reply": "I could subtitle it.",
         "passes": [{"door": "captions", "asked_for_it": True}]},
        NO_VERBS, 5, audio_tracks=0, asked_text="add captions")
    check("⚠ NO AUDIO ON THE TIMELINE, NO `captions` DOOR",
          silent["passes"] == [], str(silent["passes"]))
    loud = agent._read_turn(
        {"kind": "answer", "reply": "I could subtitle it.",
         "passes": [{"door": "captions", "asked_for_it": True}]},
        NO_VERBS, 5, audio_tracks=1, asked_text="add captions")
    check("…and with a track to read, it is offered",
          len(loud["passes"]) == 1, str(loud["passes"]))

    # ------------------------------------------------------------------- 4
    print("\n4 · ASKING TO SEE — read, ranged, and refused the second time\n")
    look = agent._coerce_look({"shots": [4, 2, 2, 99], "why": "need to see them"}, 5)
    check("a look is deduped and SORTED (the pictures travel in this order)",
          look["shots"] == [2, 4], json.dumps(look))
    check("…out-of-range shots are dropped", 99 not in look["shots"])
    check("…and a look with nothing left is no look",
          agent._coerce_look({"shots": [40, 52]}, 27) is None)
    check("…nor is junk", agent._coerce_look(None, 5) is None)
    if data:
        check("the client reads it identically",
              data["look"]["shots"] == look["shots"], json.dumps(data["look"]))
        check("…and refuses an out-of-range-only look too",
              data["lookEmpty"] is None and data["lookJunk"] is None)

    looked = agent._read_turn(
        {"kind": "answer", "reply": "let me see", "look": {"shots": [1, 2]}},
        {"verbs": [{"id": "note", "args": []}]}, 5)
    check("a look becomes a turn of its own kind", looked["kind"] == "look", looked["kind"])
    # ⚠ THE LOOP GUARD, AND IT IS IN CODE RATHER THAN IN THE PROMPT. The prompt
    # asks; only this can promise. `blind=False` is what the call carrying the
    # pictures uses.
    again = agent._read_turn(
        {"kind": "answer", "reply": "let me see again", "look": {"shots": [3, 4]}},
        {"verbs": [{"id": "note", "args": []}]}, 5, blind=False)
    check("⚠ …and a SECOND look, on the call that can already see, is refused",
          again["look"] is None and again["kind"] != "look", json.dumps(again["kind"]))
    if data:
        check("a look outranks a plan on the same reply",
              data["turnLookBeatsPlan"]["kind"] == "look",
              json.dumps(data["turnLookBeatsPlan"]["kind"]))

    # ------------------------------------------------------------------- 5
    print("\n5 · THE PICTURES REACH THE MODEL, AND THE RULES MOVE OUT OF THE WAY\n")
    board = {"title": "T", "total_ms": 20000,
             "shots": [{"label": f"s{i}", "ms": 4000} for i in range(5)], "existing": {}}
    vocab = {"verbs": [{"id": "note", "args": []}], "transitions": [{"id": "dissolve"}],
             "easings": ["linear"], "animatable": {"frame": ["scale"]}}
    seen = {}

    def spy(request):
        seen[len(seen)] = request
        return json.dumps({"kind": "answer", "reply": "stub"})

    previous = llm_json.use_adapter(spy)
    try:
        agent.chat(messages=[{"role": "user", "text": "konsa part bekar hai?"}],
                   board=board, vocabulary=vocab, settings={}, language="Hinglish")
        blind = seen[0]
        agent.chat(messages=[{"role": "user", "text": "konsa part bekar hai?"}],
                   board=board, vocabulary=vocab, settings={}, language="Hinglish",
                   pictures=({"shot": 1, "mime": "image/png", "data": b"one"},
                             {"shot": 2, "mime": "image/jpeg", "data": b"two"}))
        looking = seen[1]
    finally:
        llm_json.use_adapter(previous)

    check("an ordinary turn carries no pictures", len(blind.images) == 0)
    check("a look carries them, with their mime types",
          [r["mime"] for r in looking.images] == ["image/png", "image/jpeg"],
          json.dumps([r["mime"] for r in looking.images]))
    # ⚠ THE MEASURED FIX. 149s and failing with the long system instruction;
    # 7.5s with a short one. See this file's header for the bisection.
    check("⚠ the system instruction SHRINKS on a look",
          len(looking.system) < len(blind.system) / 5,
          f"{len(looking.system)} vs {len(blind.system)}")
    check("…to exactly `LOOK_SYSTEM`", looking.system == agent.LOOK_SYSTEM)
    check("⚠ …and the rules MOVE rather than vanish — no second rulebook",
          "WHEN TO ASK" in looking.prompt and "RESTRAINT IS THE CRAFT" in looking.prompt,
          "the rules are not in the prompt")
    check("…announced as instructions, not as background",
          "these are your instructions, not background" in looking.prompt)
    check("…and the model is told which shot each picture is",
          "in order, shot 1, 2" in looking.prompt, looking.prompt[-300:])
    check("…and told not to ask to look again",
          "DO NOT ASK TO LOOK AGAIN" in looking.prompt)

    # ⚠ A PICTURE CHANGES THE ANSWER, SO IT IS IN THE FINGERPRINT — by digest,
    # never by hashing a megabyte of bytes into the digest input.
    base = dict(system="s", prompt="p", schema={"type": "object"})
    one = llm_json.JsonRequest(**base, images=({"mime": "image/png", "data": b"a"},))
    two = llm_json.JsonRequest(**base, images=({"mime": "image/png", "data": b"b"},))
    none = llm_json.JsonRequest(**base)
    check("two different pictures are two different briefs",
          one.fingerprint() != two.fingerprint())
    check("…and the same picture twice is the same brief",
          one.fingerprint() == llm_json.JsonRequest(
              **base, images=({"mime": "image/png", "data": b"a"},)).fingerprint())
    check("…and no picture is different again", none.fingerprint() != one.fingerprint())

    # ------------------------------------------------------------------- 6
    print("\n6 · THE PROMPT TEACHES BOTH, AND THE SCHEMA OFFERS THEM\n")
    schema = agent.reply_schema(vocab)
    check("the schema offers `passes`", "passes" in schema["properties"])
    check("…with only the three real doors",
          schema["properties"]["passes"]["items"]["properties"]["door"]["enum"]
          == list(agent.PAID_DOORS))
    check("the schema offers `look`", "look" in schema["properties"])
    check("…and requires the shots on it",
          schema["properties"]["look"]["required"] == ["shots"])

    prompts = (ROOT / "prompts.yaml").read_text(encoding="utf-8")
    block = prompts[prompts.index("editor_chat:"):]
    check("the prompt says asking is not refusing", "ASK IS NOT REFUSE" in block)
    check("…names every door by its wire name",
          all(f'"{door}"' in block for door in agent.PAID_DOORS),
          str([d for d in agent.PAID_DOORS if f'"{d}"' not in block]))
    check("…forbids quoting a price or guessing a tier",
          "NEVER A PRICE AND NEVER A TIER" in block)
    check("…and caps the offers at two", "AT MOST TWO" in block)
    check("the prompt teaches that it can ask to look", "BUT YOU CAN ASK TO LOOK" in block)
    # ⚠ THE COST RULE IS THE WHOLE REASON A LOOK IS A REQUEST. Without it the
    # model looks on every turn and the cheapest question in the product becomes
    # the most expensive.
    check("…and that looking costs the person money",
          "IT COSTS THEM MONEY" in block)
    check("…with examples of when NOT to look", "none of\n      those need eyes" in block
          or "those need eyes" in block)
    check("…and that one look is all it gets", "ONE LOOK PER QUESTION" in block)
    check("…and that a look is not an edit", "A LOOK IS NOT AN EDIT" in block)

    return 1 if failures else 0


if __name__ == "__main__":
    code = main()
    print("\n" + ("FAILED: " + "; ".join(failures) if failures
                  else "All door / look checks passed — no model called, nothing spent."))
    sys.exit(code)
