"""WILL MODEL X WORK? RUN THIS AND FIND OUT. — the Director's contract check.

    python tests/director_contract_check.py

    DIRECTOR_CONTRACT_LIVE=1 \
    DIRECTOR_PROVIDER=openai_compatible \
    DIRECTOR_BASE_URL=http://localhost:11434/v1 \
    DIRECTOR_MODEL=qwen2.5:14b \
      python tests/director_contract_check.py

⚠ THIS FILE IS THE PERMANENT ANSWER TO A QUESTION THAT OTHERWISE GETS ARGUED.
"Could we run the Director on a different model" has been a matter of opinion in
this repo since Phase 2. It is a matter of opinion because the honest answer is
four properties long and nobody wants to check four properties by hand on every
candidate. So they are checked here, against ONE committed board
(`tests/golden_brief.json`), and the answer is an exit code.

The four, and why each is the one that matters:

  1. IT IS A VALID PLAN. Version, a summary, and at least one step that survived
     `validatePlan`. A model that returns beautiful prose about the film has
     failed at the only thing it was asked to do.
  2. EVERY SHOT IS COVERED. The reading has a beat for all six, and nothing
     outside the film survives into it. ⚠ This is the failure that LOOKS like
     success: a model that reads four shots and plans confidently around them
     produces a plan that validates, runs, and puts the ending in the middle.
     (A seventh shot is DROPPED by `_coerce_analysis` rather than caught here —
     what is asserted is the guarantee downstream actually gets.)
  3. NO ILLEGAL VOCABULARY. Every verb, every transition kind, every effect,
     every preset came off the manifest the browser sent. A model inventing
     `add_zoom` or `kind: "crossfade"` is not dangerous — the fence drops it —
     but a model doing it CONSTANTLY writes a plan that is 30% air, and the
     person reading the preview cannot tell which.
  4. THE GUARD RAILS ARE RESPECTED, meaning the fence had nothing to trim. Not
     "the fence caught it" — that is already proven in
     `director_guardrails_check.py` and holds whoever wrote the plan. What is
     asked here is whether the model can show RESTRAINT ON ITS OWN, because a
     preview listing 30 dissolves that the fence quietly cuts to 1 is a preview
     of a different film from the one the button makes.

---------------------------------------------------------------------------
⚠ TWO MODES, AND THE BANNER SAYS WHICH ONE YOU GOT.
---------------------------------------------------------------------------
LIVE (`DIRECTOR_CONTRACT_LIVE=1`) calls whatever provider the environment is
configured for, and the four properties above are then a statement about that
model. Two text calls, no images, no Veo, no dollar unless your text model bills
one — but it is opt-in anyway, because nothing in this repo spends because a test
suite happened to run.

REPLAY (the default) runs the same board and the same code path against a
committed model answer, deliberately wrapped the way a small model wraps things:
the reading arrives truncated inside a ```json fence and has to be repaired, the
plan arrives with "Here is the plan you asked for:" in front of it and an offer
to tighten it behind. That proves THE MACHINERY — extraction, the one repair,
folding, the language fence, validation, the guard rails — end to end, and it
proves nothing whatsoever about any model. The banner says so in those words, and
the summary line says it again, because a green tick that reads as "GPT-5 passed"
when no model was called is worse than a red one.

⚠ PART ONE RUNS IN BOTH MODES AND NEVER CALLS ANYTHING. It is the hardening
itself — the shape sketch, the brace walker, the single repair — and those are
properties of this repo's code, not of anyone's model, so they are asserted
against stubs every time.

Needs `node` for the manifest and the fence. Nothing here touches a browser, and
nothing here renders a frame.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import director
import llm_json

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "client/src/animatic/agent"
GOLDEN = Path(__file__).resolve().parent / "golden_brief.json"

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# ---------------------------------------------------------------------------
# THE BOARD, AND THE MANIFEST IT IS PLANNED AGAINST
# ---------------------------------------------------------------------------
def golden() -> dict:
    """The committed brief. Keys starting `_` are the reasoning, not the data."""
    with open(GOLDEN, encoding="utf-8") as fh:
        blob = json.load(fh)
    return {k: v for k, v in blob.items() if not k.startswith("_")}


def manifest() -> dict:
    code = (
        "import('%s').then(m=>process.stdout.write(JSON.stringify(m.capabilities())))"
        % (AGENT / "capabilities.js").as_uri()
    )
    proc = subprocess.run(["node", "-e", code], capture_output=True, text=True, encoding="utf-8")
    return json.loads(proc.stdout) if proc.returncode == 0 else {}


# ⚠ THE FENCE IS THE CLIENT'S, RUN AS THE CLIENT RUNS IT. Rebuilding the caps
# arithmetic in Python would give a second answer that is right today and wrong
# the first time a cap moves — the same reasoning that keeps the VOCABULARY on
# the browser side (see `director.py`'s header).
HARNESS = """
import { readFileSync } from "node:fs";
import { capabilities, HOUSE_CAPS } from "__CAPS__";
import { applyGuardrails } from "__HOUSE__";
import { validatePlan, planTotals } from "__SCHEMA__";

const input = JSON.parse(readFileSync(process.argv[2], "utf8"));
const caps = capabilities();

// The read-model the editor would hand the runner, built from the golden board.
const frames = input.shots.map((s, i) => ({
  id: `f${i + 1}`, duration_ms: s.ms, label: s.label,
}));
const starts = [];
let at = 0;
for (const f of frames) { starts.push(at); at += f.duration_ms; }
const ctx = {
  frames, starts, texts: [], shapes: [], transitions: [], overlays: [],
  audioTracks: [], totalMs: at, caps,
};

const checked = validatePlan(input.plan, caps, ctx);
const fenced = applyGuardrails(checked.plan, ctx);

process.stdout.write(JSON.stringify({
  houseCaps: HOUSE_CAPS,
  totalMs: at,
  dropped: checked.dropped,
  trimmed: fenced.trimmed,
  validated: checked.plan.steps.map((s) => ({ verb: s.verb, args: s.args })),
  kept: fenced.plan.steps.map((s) => ({ verb: s.verb, args: s.args })),
  totals: planTotals(fenced.plan),
}));
"""


def run_fence(plan: dict, shots: list[dict]) -> dict | None:
    """`validatePlan` → `applyGuardrails`, in node, on the golden board."""
    if not shutil.which("node"):
        return None
    work = tempfile.mkdtemp(prefix="director_contract_")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(
                HARNESS.replace("__CAPS__", (AGENT / "capabilities.js").as_uri())
                .replace("__HOUSE__", (AGENT / "house_style.js").as_uri())
                .replace("__SCHEMA__", (AGENT / "plan_schema.js").as_uri())
            )
        payload = os.path.join(work, "plan.json")
        with open(payload, "w", encoding="utf-8") as fh:
            json.dump({"plan": plan, "shots": shots}, fh)
        proc = subprocess.run(
            ["node", harness, payload],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:1500])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# THE REPLAY ANSWER — what a competent model sends, wrapped the way one does
# ---------------------------------------------------------------------------
# ⚠ IT IS RESTRAINED ON PURPOSE, AND THE NUMBERS ARE THE REASON. Six shots and
# five cuts buy ONE transition, TWO treated shots and two captions (see the
# `_why` in golden_brief.json). This answer spends one transition on the one real
# scene boundary, treats two shots, and writes one title — so a REPLAY run that
# reports "the fence trimmed nothing" is reporting something true about this
# answer rather than something true about the caps.
GOLDEN_ANSWER = {
    "analyse": {
        "logline": "A man is told no, and goes to ask someone else.",
        "mood": "restrained",
        "genre": "urban drama",
        "scenes": [
            {"start_shot": 1, "end_shot": 3, "title": "The rooftop",
             "why": "one place, one waiting; it ends when the call does"},
            {"start_shot": 4, "end_shot": 6, "title": "The descent",
             "why": "he moves, and the film moves with him"},
        ],
        "shots": [
            {"shot": 1, "beat": "the city, and how small he is in it",
             "emphasis": "normal", "motion": "Static wide on a grey skyline at dawn.",
             "dialogue": ""},
            {"shot": 2, "beat": "he is putting off the call", "emphasis": "low",
             "motion": "Slow handheld closer on his hands and the phone.", "dialogue": ""},
            {"shot": 3, "beat": "the no — the film's centre", "emphasis": "high",
             "motion": "Slow push in on his face as he listens.",
             "dialogue": "They said no. Again."},
            {"shot": 4, "beat": "the decision, taken at speed", "emphasis": "normal",
             "motion": "Handheld following him down a stairwell, lights passing.",
             "dialogue": ""},
            {"shot": 5, "beat": "the world going the other way", "emphasis": "low",
             "motion": "Static as a crowd passes across frame.", "dialogue": ""},
            {"shot": 6, "beat": "he asks again", "emphasis": "high",
             "motion": "Low static angle looking up at a building front.",
             "dialogue": "Then I'll ask someone else."},
        ],
        "title_card": "Ask Someone Else",
        "notes": ["Nothing says what he is asking for — leave it that way."],
    },
    "polish": {
        "summary": "Hold the call, then cut hard into the descent.",
        "mood": "restrained",
        "steps": [
            {"verb": "note",
             "args": {"text": "Two scenes. The only soft cut is where the rooftop ends."},
             "note": ""},
            {"verb": "add_transition",
             "args": {"cut": 3, "kind": "dissolve", "ms": 800},
             "note": "the rooftop ends here and nowhere else"},
            {"verb": "push_in",
             "args": {"shot": 3, "from": 1, "to": 1.06, "ease": "ease-in-out"},
             "note": "the call is the film"},
            {"verb": "add_effect",
             "args": {"shot": 1, "kind": "exposure",
                      "params": [{"name": "amount", "value": "-0.15"}]},
             "note": "dawn, not daylight"},
            {"verb": "add_effect",
             "args": {"shot": 6, "kind": "contrast",
                      "params": [{"name": "amount", "value": "1.1"}]},
             "note": "he is decided now"},
            {"verb": "add_text",
             "args": {"shot": 1, "text": "Ask Someone Else", "ref": "t1",
                      "position": "bottom", "size": "medium"},
             "note": "title over the establishing wide"},
            {"verb": "apply_text_preset",
             "args": {"ref": "t1", "preset": "fade", "inMs": 400, "outMs": 400},
             "note": "in and out, quietly"},
        ],
    },
}


def replay_adapter(seen: dict):
    """A model that wraps its answers — and gets the first one wrong.

    ⚠ THE FIRST READING IS TRUNCATED INSIDE A FENCE, which is the failure a small
    model actually produces (it hits a token ceiling mid-object), and it is the
    one the repair exists for. The plan then arrives with prose on both sides,
    which is the failure the brace walker exists for. One replay run therefore
    exercises both halves of the hardening rather than describing them.
    """
    def adapter(request):
        n = seen[request.purpose] = seen.get(request.purpose, 0) + 1
        answer = GOLDEN_ANSWER[request.purpose]
        if request.purpose == "analyse" and n == 1:
            return "Sure — here's the reading:\n\n```json\n" + json.dumps(answer)[:240] + "\n"
        if request.purpose == "polish" and n == 1:
            return (
                "Here is the plan you asked for:\n\n```json\n"
                + json.dumps(answer, indent=2)
                + "\n```\n\nHappy to tighten it if that is too much."
            )
        return json.dumps(answer)
    return adapter


# ---------------------------------------------------------------------------
# PART ONE — THE HARDENING. No model, either mode, every run.
# ---------------------------------------------------------------------------
def check_hardening(caps: dict):
    print("\n⚠ THE SCHEMA, WRITTEN INTO THE PROMPT — for a model that cannot be handed one\n")

    schema = director.plan_schema(caps)
    sketch = llm_json.schema_prose(schema)
    verbs = [v["id"] for v in caps["verbs"]]

    check("every verb the manifest declares is offered to the model by name",
          all(f'"{v}"' in sketch for v in verbs),
          str([v for v in verbs if f'"{v}"' not in sketch])[:200])
    check("the required keys are marked as required, not merely listed",
          '"summary": <string>' in sketch and "required" in sketch)
    check("a nested object keeps its shape rather than collapsing to <object>",
          '"verb":' in sketch and '"args": {' in sketch)
    check("an array says it is one", '"steps": [' in sketch)
    check("⚠ THE SKETCH IS THE SAME TEXT TWICE — it is part of a prompt that is "
          "hashed for the determinism claim",
          llm_json.schema_prose(schema) == sketch)

    analyse_sketch = llm_json.schema_prose(director.analyse_schema())
    check("the analyse schema renders its enum as a choice, not as <string>",
          '<"low" | "normal" | "high">' in analyse_sketch, analyse_sketch[:200])

    print("\n⚠ WHAT COMES BACK IS EXTRACTED BEFORE IT IS PARSED\n")
    cases = [
        ("a bare object", '{"a": 1}', {"a": 1}),
        ("a ```json fence", '```json\n{"a": 1}\n```', {"a": 1}),
        ("a bare ``` fence", '```\n{"a": 1}\n```', {"a": 1}),
        ("prose in front", 'Here you go:\n{"a": 1}', {"a": 1}),
        ("prose behind", '{"a": 1}\nHope that helps!', {"a": 1}),
        ("prose both sides, fenced", 'Sure:\n```json\n{"a": 1}\n```\nAnything else?', {"a": 1}),
        # ⚠ THE ONE THAT NEEDS THE FENCE LOOP AND NOT JUST THE WALKER. Every case
        # above survives with fence-stripping deleted, because the braces are
        # still the first braces. Here they are not: the sentence in front has a
        # `{` of its own, and a walker starting at the first one reads `{n}`.
        ("a brace in the prose in front of the fence",
         'Note: I used {n} for the shot number.\n```json\n{"a": 1}\n```', {"a": 1}),
        ("nested objects", '{"a": {"b": {"c": 1}}}', {"a": {"b": {"c": 1}}}),
    ]
    for label, payload, want in cases:
        got = llm_json.extract_json(payload)
        check(f"reads through {label}", json.loads(got) == want, repr(got)[:120])

    # ⚠ THE ONE THE REGEX VERSION GETS WRONG, and the reason the walker exists:
    # this is not a contrived string, it is what an edit plan CONTAINS — the
    # user's own words, on screen, in a caption.
    tricky = '{"steps": [{"verb": "add_text", "args": {"text": "see you } later"}}], "summary": "x"}'
    check("⚠ A `}` INSIDE A CAPTION DOES NOT END THE OBJECT",
          json.loads(llm_json.extract_json(tricky + "\n\nDone."))["summary"] == "x")
    check("...and neither does an escaped quote before one",
          json.loads(llm_json.extract_json(r'{"t": "say \"hi\" }", "ok": 1}'))["ok"] == 1)
    check("a truncated object comes back truncated, so json.loads can say where",
          llm_json.extract_json('{"a": 1, "b":').startswith("{"))
    check("an answer with no JSON in it at all is not turned into one",
          llm_json.extract_json("I cannot help with that.") == "I cannot help with that.")

    # -----------------------------------------------------------------------
    # ⚠ AN ANSWER THAT STOPPED HALFWAY IS NOT AN ANSWER OF NOTHING.
    # -----------------------------------------------------------------------
    # Live on 2026-09-06, in a red banner over an 8-shot promo: *"shots 1–8: The
    # model returned unusable JSON for the editor chat batch call — it would not
    # parse: Unterminated string starting at: line 6 column 16 (char 87)."* One
    # caption in the middle never closed its quote, and every COMPLETE step
    # before it went in the bin with it — then the batch, then the job.
    def salvaged(text):
        return llm_json.salvage_json(llm_json.extract_json(text))

    live = ('{"steps": ['
            '{"verb": "add_text", "args": {"shot": 1, "text": "Hungry at work?"}}, '
            '{"verb": "add_text", "args": {"shot": 2, "text": "Nothing in the fridge"}}, '
            '{"verb": "add_text", "args": {"shot": 3, "text": "High delive')
    kept = salvaged(live)
    check("⚠ A CUT-OFF ANSWER KEEPS THE STEPS THAT DID ARRIVE",
          kept and len(kept["steps"]) == 2, json.dumps(kept)[:140])
    check("…and they are exactly what the model wrote, untouched",
          kept["steps"][1]["args"]["text"] == "Nothing in the fridge",
          json.dumps(kept["steps"][1]))
    # ⚠ **AND NOT THE HALF-BUILT ONE.** The third step really did carry
    # `"verb": "add_text"` and `"shot": 3` before the answer stopped, so a naive
    # cut keeps it — a caption step with no caption, which every validator
    # downstream then reports as a mistake the model never actually made.
    check("⚠ …AND NEVER A STEP THE MODEL DID NOT FINISH",
          all(s["args"].get("text") for s in kept["steps"]), json.dumps(kept))

    # ⚠ IT CUTS, IT NEVER MENDS — so an answer with nothing complete in it is
    # still a failure, and must stay one. Salvaging `{"kind": "plan"}` out of a
    # cut-off turn would put an empty reply on screen and call it success.
    for label, broken in (
        ("nothing complete at all", '{"steps": [{"verb'),
        ("an empty list and no more", '{"steps": ['),
        ("a turn cut inside its reply", '{"kind": "plan", "reply": "I will add the ti'),
        ("a key with no value", '{"kind": "plan", "repl'),
    ):
        check(f"⚠ …and {label} is STILL a failure", salvaged(broken) is None,
              repr(salvaged(broken)))

    check("an escaped quote inside a rescued caption survives it",
          salvaged(r'{"steps": [{"text": "say \"hi\""}, {"text": "unclo'
                   ) == {"steps": [{"text": 'say "hi"'}]},
          repr(salvaged(r'{"steps": [{"text": "say \"hi\""}, {"text": "unclo')))
    check("a whole plan whose LAST field was cut keeps the plan",
          (salvaged('{"kind":"plan","plan":{"summary":"titles","steps":'
                    '[{"verb":"note","args":{}}]},"soun') or {}).get("plan", {})
          .get("summary") == "titles")
    check("an answer that was already valid is returned unchanged",
          salvaged('{"steps": [{"verb": "note", "args": {}}], "note": "ok"}')
          == {"steps": [{"verb": "note", "args": {}}], "note": "ok"})

    print("\n⚠ AND A BROKEN ANSWER BUYS EXACTLY ONE REPAIR\n")
    request = llm_json.JsonRequest(
        system="s", prompt="p", schema={"type": "object",
                                        "properties": {"ok": {"type": "integer"}},
                                        "required": ["ok"]},
        purpose="polish",
    )

    # ⚠ THE BACKOFF IS TURNED OFF FOR THESE THREE CHECKS. The retry policy is
    # 4s then 8s; asserting the CALL COUNT does not need twelve seconds of sleep,
    # and a test nobody runs because it is slow proves nothing.
    backoff = llm_json.INITIAL_BACKOFF_SECONDS
    llm_json.INITIAL_BACKOFF_SECONDS = 0
    mode = os.environ.get("DIRECTOR_STRUCTURED_OUTPUT")
    os.environ["DIRECTOR_STRUCTURED_OUTPUT"] = "prompt"
    # ⚠ THE WARNINGS ARE TURNED DOWN FOR THIS BLOCK ONLY. Every failure below is
    # engineered, and eight "could not be read" lines above the banner read as a
    # broken test rather than as the test working. The golden run further down
    # keeps its logging, because the repair it does there is real evidence.
    noisy = logging.getLogger("llm_json")
    was = noisy.level
    noisy.setLevel(logging.ERROR)
    try:
        sent = []

        def mends(req):
            sent.append(req)
            return '{"ok": 1}' if len(sent) > 1 else "Here: {oops"

        previous = llm_json.use_adapter(mends)
        got = llm_json.complete_json(request)
        llm_json.use_adapter(previous)
        check("the mended answer is the one that is returned", got == {"ok": 1})
        check("it cost two calls — the original and one repair", len(sent) == 2, str(len(sent)))
        check("⚠ THE REPAIR HANDS THE MODEL ITS OWN WORDS BACK, not the question again",
              "{oops" in sent[1].prompt and "WHAT WENT WRONG" in sent[1].prompt)
        check("...and the shape it should have been, one more time",
              '"ok": <integer>' in sent[1].prompt)
        check("the first call already carried the schema in the prompt",
              "RETURN ONE JSON OBJECT" in sent[0].prompt)

        broken = []

        def never(req):
            broken.append(req)
            return "nope, not JSON"

        previous = llm_json.use_adapter(never)
        try:
            llm_json.complete_json(request)
            check("a model that cannot do this raises rather than returns junk", False)
        except llm_json.LLMJsonError as e:
            check("a model that cannot do this raises, with a reason for a human",
                  "unusable JSON" in str(e), str(e)[:160])
        llm_json.use_adapter(previous)
        check("⚠ ONE REPAIR PER CALL, NOT ONE PER RETRY — 3 attempts + 1 repair = 4",
              len(broken) == llm_json.MAX_RETRIES + 1, f"{len(broken)} calls")

        empty = []

        def says_nothing(req):
            empty.append(req)
            return "   "

        previous = llm_json.use_adapter(says_nothing)
        try:
            llm_json.complete_json(request)
        except llm_json.LLMJsonError:
            pass
        llm_json.use_adapter(previous)
        check("⚠ AN EMPTY ANSWER BUYS NO REPAIR — there is nothing to mend, and a "
              "safety block does not un-block on being asked twice",
              len(empty) == llm_json.MAX_RETRIES, f"{len(empty)} calls")
    finally:
        noisy.setLevel(was)
        llm_json.INITIAL_BACKOFF_SECONDS = backoff
        if mode is None:
            os.environ.pop("DIRECTOR_STRUCTURED_OUTPUT", None)
        else:
            os.environ["DIRECTOR_STRUCTURED_OUTPUT"] = mode

    print("\n⚠ WHICH WAY THE SCHEMA TRAVELS IS A SETTING, AND IT IS PART OF THE CALL\n")
    for provider, want in (("vertex", "native"), ("gemini", "native"),
                           ("stub", "native"), ("openai_compatible", "prompt")):
        check(f"auto → {want} on {provider}", llm_json.schema_mode(provider) == want)
    check("`openai` is accepted as the name people will type",
          llm_json.resolve_provider("openai") == "openai_compatible")

    native = director.analyse_request(director.build_brief(golden()["board"]))
    prompted = llm_json.as_prompt_schema(native)
    check("⚠ THE PROMPT-MODE CALL HAS A DIFFERENT FINGERPRINT — it is a different "
          "call, and the determinism claim has to know that",
          native.fingerprint() != prompted.fingerprint())
    check("...and the same fingerprint as itself, twice",
          prompted.fingerprint() == llm_json.as_prompt_schema(native).fingerprint())
    check("the schema is still on the request, so nothing downstream loses it",
          prompted.schema == native.schema)
    check("⚠ AND THE WHOLE BOARD IS STILL IN THERE. The contract is appended, "
          "never substituted for the film",
          all(s["label"].strip() in prompted.prompt
              for s in golden()["board"]["shots"]),
          str([s["label"] for s in golden()["board"]["shots"]
               if s["label"].strip() not in prompted.prompt]))


# ---------------------------------------------------------------------------
# PART ONE AND A HALF — THE WIRE. Still no model; a fake `requests.post`.
# ---------------------------------------------------------------------------
class _Fake:
    """One canned HTTP response, with the two things the adapter reads."""

    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text or json.dumps(body or {})

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


def check_wire():
    """What actually goes down the socket to somebody else's endpoint.

    ⚠ "WILL MODEL X WORK" IS HALF A QUESTION ABOUT US. A model cannot answer a
    request we sent wrongly, and the failure looks identical from the outside —
    a 400, or an answer that ignores the system prompt. So the request is
    assembled here against a fake transport and read field by field, which is
    the one part of the non-Google path that can be pinned without a network.
    """
    print("\n⚠ THE OPENAI-SHAPED CALL, FIELD BY FIELD — no SDK, one POST\n")
    import requests

    sent: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.clear()
        sent.update({"url": url, "body": json, "headers": headers, "timeout": timeout})
        return _Fake(body={"choices": [{"message": {"content": '{"ok": 1}'}}]})

    request = llm_json.JsonRequest(
        system="you are an editor", prompt="the board", schema={"type": "object"},
        purpose="polish",
    )
    keep = {k: os.environ.get(k) for k in
            ("DIRECTOR_PROVIDER", "DIRECTOR_MODEL", "DIRECTOR_BASE_URL",
             "DIRECTOR_API_KEY", "DIRECTOR_JSON_MODE")}
    real_post = requests.post
    try:
        requests.post = fake_post
        os.environ["DIRECTOR_PROVIDER"] = "openai_compatible"
        os.environ["DIRECTOR_BASE_URL"] = "http://localhost:11434/v1/"
        os.environ["DIRECTOR_MODEL"] = "qwen2.5:14b"
        os.environ.pop("DIRECTOR_API_KEY", None)
        os.environ.pop("DIRECTOR_JSON_MODE", None)

        got = llm_json._openai_adapter(request)
        body = sent["body"]
        check("the answer is read out of choices[0].message.content", got == '{"ok": 1}')
        check("it posts to {base}/chat/completions, with the trailing slash tidied",
              sent["url"] == "http://localhost:11434/v1/chat/completions", sent["url"])
        check("the model is the one you named, not the text model's Google id",
              body["model"] == "qwen2.5:14b")
        check("the system instruction is the system message, and it is first",
              body["messages"][0] == {"role": "system", "content": "you are an editor"})
        check("the prompt is the user message",
              body["messages"][1] == {"role": "user", "content": "the board"})
        check("⚠ THE SAMPLING TRAVELS — greedy and seeded, same as on Google, or "
              "the determinism claim quietly stops applying off-Google",
              body["temperature"] == 0.0 and body["top_p"] == 1.0 and body["seed"] == 42,
              json.dumps({k: body.get(k) for k in ("temperature", "top_p", "seed")}))
        check("json mode is asked for by default",
              body.get("response_format") == {"type": "json_object"})
        check("no Authorization header when there is no key (a local model wants none)",
              "Authorization" not in sent["headers"])
        check("the timeout is set, so a hung local model is not forever",
              sent["timeout"] == llm_json.DEFAULT_TIMEOUT_SECONDS, str(sent["timeout"]))

        os.environ["DIRECTOR_API_KEY"] = "sk-test"
        os.environ["DIRECTOR_JSON_MODE"] = "off"
        llm_json._openai_adapter(request)
        check("a key becomes a bearer token", sent["headers"].get("Authorization") == "Bearer sk-test")
        check("DIRECTOR_JSON_MODE=off really does drop the field, for endpoints "
              "that reject it", "response_format" not in sent["body"])

        def refuses(url, json=None, headers=None, timeout=None):
            return _Fake(status=400, text='{"error":{"message":"unknown model"}}')

        requests.post = refuses
        try:
            llm_json._openai_adapter(request)
            check("an HTTP error is raised, not returned", False)
        except llm_json.LLMJsonError as e:
            check("⚠ AN HTTP ERROR QUOTES THE BODY. Half these endpoints are "
                  "somebody's own deployment, and \"400\" alone sends the reader "
                  "to the wrong place",
                  "unknown model" in str(e) and "400" in str(e), str(e)[:160])

        def rambles(url, json=None, headers=None, timeout=None):
            return _Fake(body={"result": "hello"})

        requests.post = rambles
        try:
            llm_json._openai_adapter(request)
            check("an endpoint that is not OpenAI-shaped says so", False)
        except llm_json.LLMJsonError as e:
            check("an endpoint that is not OpenAI-shaped says so, by name",
                  "OpenAI-compatible" in str(e), str(e)[:160])

        os.environ.pop("DIRECTOR_MODEL")
        try:
            llm_json.model_id()
            check("⚠ A MISSING DIRECTOR_MODEL IS A SENTENCE BEFORE THE CALL, not a "
                  "404 after it", False)
        except llm_json.LLMJsonError as e:
            check("⚠ A MISSING DIRECTOR_MODEL IS A SENTENCE BEFORE THE CALL, not a "
                  "404 after it", "DIRECTOR_MODEL" in str(e), str(e)[:160])
    finally:
        requests.post = real_post
        for key, value in keep.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# PART TWO — THE GOLDEN BRIEF, THROUGH WHOEVER IS CONFIGURED
# ---------------------------------------------------------------------------
def live_wanted() -> bool:
    return (os.environ.get("DIRECTOR_CONTRACT_LIVE") or "").strip().lower() in ("1", "true", "yes", "on")


def run_golden(caps: dict, live: bool) -> tuple[dict | None, str]:
    """The whole pass on the golden brief. Returns `(result, reason-if-none)`."""
    brief = golden()
    seen: dict = {}
    previous = None if live else llm_json.use_adapter(replay_adapter(seen))
    try:
        out = director.direct(
            board=brief["board"],
            vocabulary=caps,
            include=brief["include"],
            language=brief["language"],
            brief_text=brief["brief"],
        )
    except director.DirectorError as e:
        return None, str(e)
    except llm_json.LLMJsonError as e:
        return None, str(e)
    finally:
        if not live:
            llm_json.use_adapter(previous)
    if not live:
        out["_calls"] = dict(seen)
    return out, ""


def check_golden(caps: dict, live: bool):
    brief = golden()
    board = brief["board"]
    shots = board["shots"]

    print("\n⚠ THE GOLDEN BRIEF IS READ WHOLE — every shot, nothing summarised away\n")
    built = director.build_brief(board, brief_text=brief["brief"], language=brief["language"])
    check("six shots in, six shots on the brief", built["shot_count"] == len(shots) == 6,
          str(built["shot_count"]))
    check("each one keeps its own hold", [s["holds_ms"] for s in built["shots"]]
          == [s["ms"] for s in shots])
    check("the film's length is the sum of them", built["total_ms"] == sum(s["ms"] for s in shots))
    check("the person's own sentence is in the brief", brief["brief"][:20] in built["brief"])
    check("nothing is on the timeline yet, and the model is told so",
          built["already_on_it"]["transitions_on_cuts"] == []
          and built["already_on_it"]["text_clips"] == 0)

    print(f"\n⚠ AND NOW THE PLAN, FROM {'THE CONFIGURED MODEL' if live else 'THE REPLAY ANSWER'}\n")
    out, reason = run_golden(caps, live)
    if out is None:
        check("the provider wrote a plan at all", False, reason)
        print("\n  ⚠ THAT IS THE ANSWER TO 'WILL THIS MODEL WORK': no, and the reason")
        print("    above is the one a user would have been shown in the dialog.")
        return
    check("the provider wrote a plan at all", True)

    if not live:
        calls = out.get("_calls") or {}
        check("⚠ THE REPLAY'S TRUNCATED READING REALLY WAS REPAIRED — two analyse "
              "calls, one of them the mend",
              calls.get("analyse") == 2, json.dumps(calls))
        check("...and the fenced plan needed no repair, because extraction handled it",
              calls.get("polish") == 1, json.dumps(calls))

    plan = out["plan"]
    analysis = out["analysis"]

    # ------------------------------------------------------- 1. A VALID PLAN
    print("\n⚠ ONE — IT IS A VALID PLAN\n")
    check("it carries the plan version the runner expects", plan.get("version") == 1)
    check("it says in one sentence what it does", bool((plan.get("summary") or "").strip()),
          repr(plan.get("summary"))[:120])
    check("it has steps", len(plan.get("steps") or []) > 0, str(len(plan.get("steps") or [])))
    check("every step names a verb and carries arguments",
          all(s.get("verb") and s.get("args") for s in plan["steps"]))
    check("the language it was written in is on it", plan.get("language") == brief["language"])
    check("the include flags it was given are on it too",
          plan.get("include", {}).get("veo") is False)

    # -------------------------------------------------- 2. EVERY SHOT COVERED
    print("\n⚠ TWO — EVERY SHOT IS COVERED, AND NO SEVENTH ONE IS INVENTED\n")
    read = [s["shot"] for s in analysis.get("shots") or []]
    check("⚠ THE READING HAS A BEAT FOR ALL SIX — the failure that looks like "
          "success is a confident plan built on four of them",
          sorted(read) == [1, 2, 3, 4, 5, 6], str(sorted(read)))
    check("every beat says something", all((s.get("beat") or "").strip()
                                           for s in analysis.get("shots") or []))
    check("no shot is read twice", len(read) == len(set(read)))
    check("⚠ AND NO SHOT OUTSIDE THE FILM SURVIVES INTO THE READING. `_coerce_"
          "analysis` drops one, so this is the guarantee downstream relies on "
          "rather than a claim about the model's manners",
          all(1 <= n <= 6 for n in read), str(sorted(read)))
    check("the scenes it found stay inside the film",
          all(1 <= s["start_shot"] <= s["end_shot"] <= 6 for s in analysis.get("scenes") or []),
          json.dumps(analysis.get("scenes"))[:200])
    addressed = [s["args"]["shot"] for s in plan["steps"] if "shot" in s["args"]]
    check("no step addresses a shot that is not in the film",
          all(1 <= int(n) <= 6 for n in addressed), str(sorted(set(addressed))))
    cuts = [s["args"]["cut"] for s in plan["steps"] if "cut" in s["args"]]
    check("no step addresses a cut that is not in the film (six shots is five cuts)",
          all(1 <= int(c) <= 5 for c in cuts), str(sorted(set(cuts))))

    # ---------------------------------------------- 3. NO ILLEGAL VOCABULARY
    print("\n⚠ THREE — EVERY WORD IN THE PLAN CAME OFF THE MANIFEST\n")
    known = {
        "verbs": {v["id"] for v in caps["verbs"]},
        "transitions": {t["id"] for t in caps["transitions"]},
        "effects": {e["id"] for e in caps["effects"]},
        "shapes": {s["id"] for s in caps["shapes"]},
        "presets": {p["id"] for p in caps["text"]["presets"]},
        "easings": set(caps["easings"]),
    }
    bad_verbs = [s["verb"] for s in plan["steps"] if s["verb"] not in known["verbs"]]
    check("every verb exists in this build", not bad_verbs, str(bad_verbs))

    families = {"add_transition": "transitions", "add_effect": "effects", "add_shape": "shapes"}
    bad_kinds = [
        f'{s["verb"]}:{s["args"]["kind"]}'
        for s in plan["steps"]
        if s["verb"] in families and "kind" in s["args"]
        and s["args"]["kind"] not in known[families[s["verb"]]]
    ]
    check("every transition, effect and shape kind is one this build renders",
          not bad_kinds, str(bad_kinds))
    bad_presets = [s["args"]["preset"] for s in plan["steps"]
                   if "preset" in s["args"] and s["args"]["preset"] not in known["presets"]]
    check("every text preset is a real one", not bad_presets, str(bad_presets))
    bad_ease = [s["args"]["ease"] for s in plan["steps"]
                if "ease" in s["args"] and s["args"]["ease"] not in known["easings"]]
    check("every easing is a real one", not bad_ease, str(bad_ease))

    vocab_drops = [d for d in out["dropped"] if "there is no" in d.get("why", "")]
    check("⚠ NOTHING WAS DROPPED FOR BEING A WORD THIS BUILD DOES NOT HAVE",
          not vocab_drops, json.dumps(vocab_drops)[:300])

    print("\n   …and the two content rules, which are about what it WROTE\n")
    language_drops = [d for d in out["dropped"] if "written in" in d.get("why", "")]
    check("on-screen text is in the film's own script — nothing was fenced out",
          not language_drops, json.dumps(language_drops)[:300])
    motion_drops = [d for d in out["dropped"] if d.get("verb") == "veo_prompt"]
    check("the Veo motion prompts came back in English",
          not motion_drops, json.dumps(motion_drops)[:300])
    check("...and there is one for every shot that was read",
          len(out["veo"]) == len(analysis.get("shots") or []),
          f'{len(out["veo"])} prompts for {len(analysis.get("shots") or [])} shots')

    # ------------------------------------------------ 4. GUARD RAILS RESPECTED
    print("\n⚠ FOUR — THE FENCE HAD NOTHING TO DO\n")
    fence = run_fence(plan, shots)
    if fence is None:
        print("  node is not on PATH, or the agent modules would not load — the fence")
        print("  half of this check did not run. ⚠ THAT IS NOT A PASS.")
        failures.append("the fence could not be run")
        return

    check("⚠ THE VALIDATOR DROPPED NOTHING. A step that cannot land is a step the "
          "preview lists and the run skips",
          not fence["dropped"], json.dumps(fence["dropped"])[:400])
    check("⚠ AND THE FENCE TRIMMED NOTHING — restraint the model showed itself, "
          "not restraint imposed on it afterwards",
          not fence["trimmed"], json.dumps(fence["trimmed"])[:400])
    check("so the plan the user reads is step for step the plan that runs",
          len(fence["kept"]) == len(plan["steps"]),
          f'{len(plan["steps"])} written, {len(fence["kept"])} survive')

    caps_table = fence["houseCaps"]
    totals = fence["totals"]
    minutes = fence["totalMs"] / 60000.0
    print(f"\n   the plan, against the house budget for {fence['totalMs']}ms of film:\n")
    print(f"     {totals['steps']:>3} steps")
    print(f"     {totals['transitions']:>3} transitions   (5 cuts × "
          f"{caps_table['TRANSITION_CUT_SHARE']} = {int(5 * caps_table['TRANSITION_CUT_SHARE'])})")
    print(f"     {totals['effects']:>3} effects       (6 clips × "
          f"{caps_table['EFFECT_CLIP_SHARE']} = {int(6 * caps_table['EFFECT_CLIP_SHARE'])})")
    print(f"     {totals['texts']:>3} texts         ({caps_table['TEXTS_PER_MINUTE']}/min × "
          f"{minutes:.2f} = {int(caps_table['TEXTS_PER_MINUTE'] * minutes)})")
    print(f"     {totals['shapes']:>3} shapes        ({caps_table['SHAPES_PER_MINUTE']}/min × "
          f"{minutes:.2f} = {int(caps_table['SHAPES_PER_MINUTE'] * minutes)})")
    print()
    check("at least one thing was actually done to the film — a plan of pure "
          "`note` steps validates and edits nothing",
          totals["steps"] > totals.get("notes", 0) and any(
              s["verb"] != "note" for s in fence["kept"]))

    # ----------------------------------------------------------- DETERMINISM
    print("\n⚠ AND THE SAME BRIEF ASKS THE SAME QUESTION TWICE\n")
    a = director.analyse_request(director.build_brief(
        board, brief_text=brief["brief"], language=brief["language"]))
    b = director.analyse_request(director.build_brief(
        board, brief_text=brief["brief"], language=brief["language"]))
    check("the request built from the golden brief is byte-identical",
          a.fingerprint() == b.fingerprint())
    if not live:
        second, _ = run_golden(caps, live=False)
        check("...and against a fixed answer the whole plan is identical too",
              json.dumps(second["plan"], sort_keys=True) == json.dumps(plan, sort_keys=True))
    else:
        print("  ⚠ NOT ASSERTED LIVE, AND IT WILL NOT BE. No endpoint promises")
        print("    bit-exact decoding; greedy + seeded makes two runs COMPARABLE.")


# ---------------------------------------------------------------------------
def main():
    caps = manifest()
    if not caps.get("verbs"):
        print("  node is not on PATH, or the agent modules would not load — nothing checked.")
        return 1

    live = live_wanted()
    provider = llm_json.resolve_provider()
    print()
    if live:
        try:
            model = llm_json.model_id()
        except llm_json.LLMJsonError as e:
            print(f"  ⚠ LIVE was asked for and cannot run: {e}")
            return 1
        print("  ┌─────────────────────────────────────────────────────────────────┐")
        print("  │ LIVE. The golden brief goes to a real model.                    │")
        print("  └─────────────────────────────────────────────────────────────────┘")
        print(f"    provider {provider} · model {model} · schema {llm_json.schema_mode()}")
        print("    Two text calls. No images, no Veo, no render.")
    else:
        print("  ┌─────────────────────────────────────────────────────────────────┐")
        print("  │ REPLAY. ⚠ NO MODEL IS CALLED, AND NOTHING HERE IS EVIDENCE      │")
        print("  │ ABOUT ANY MODEL. What is proved is the machinery around one.    │")
        print("  └─────────────────────────────────────────────────────────────────┘")
        print("    To ask a real model instead:  DIRECTOR_CONTRACT_LIVE=1 python "
              "tests/director_contract_check.py")
        print(f"    (it would go to: provider {provider} · schema {llm_json.schema_mode()})")

    check_hardening(caps)
    check_wire()
    check_golden(caps, live)

    print()
    if failures:
        print(f"{len(failures)} FAILED:")
        for name in failures:
            print("  -", name)
        if live:
            print("\n⚠ A FAILURE HERE IS AN ANSWER, NOT A BROKEN TEST. This model does not")
            print("  clear the Director's contract on this board.")
        return 1
    print("All checks passed.")
    if not live:
        print("⚠ AND NO MODEL WAS CALLED. This says the Director's machinery holds;")
        print("  it says nothing at all about whether model X can write a plan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
