"""THE CLOCK ON A MODEL CALL, AND THE TWO KNOBS THAT DECIDE WHERE IT LANDS.

    python tests/director_timeout_check.py

⚠ WHAT THIS IS ABOUT. The 🎬 panel showed this over a fallback plan:

    "The AI pass didn't run (The server didn't respond within 120s. It may be
     stuck (a database it needs can do this) — check the backend's log, then try
     again.), so this is the rhythm read off the timeline."

Nothing was stuck and no database was involved. Three numbers simply did not
agree with each other:

  · the browser aborted the request after 120s (`REQUEST_TIMEOUT_MS`, api.js),
  · one attempt was allowed 180s (`DIRECTOR_TIMEOUT_SECONDS`) — on the OpenAI
    path only; the GOOGLE adapter, which is the default provider, passed no
    timeout to `google-genai` at all and could hang the worker thread for ever,
  · and `complete_json` would make three of those attempts with 4s + 8s of
    backoff between them. Nine and a half minutes, per call, and the plan route
    makes TWO.

So a plan that was working perfectly could not finish inside the browser's
patience, and a plan that was wedged never finished at all.

What is checked here:

  1. `call_timeout()` returns the ceiling when nothing set a deadline, and the
     REMAINING budget when something did — never more, never less than
     `MIN_ATTEMPT_SECONDS`.
  2. A call whose budget is spent stops retrying instead of sleeping into a
     browser that has already given up, and says the clock is why.
  3. The reason a caller sees names the clock ONLY when the clock is what
     stopped it — an unusable answer must not be reported as a timeout.
  4. The two numbers still agree: 2 × the server's per-call budget fits inside
     what `client/src/api.js` waits for on the plan route.
  5. The two settings that decide how long a healthy plan takes are SET and are
     in the fingerprint — `DIRECTOR_THINKING_TOKENS` and
     `DIRECTOR_MAX_OUTPUT_TOKENS`. Both were unset, which is the latency itself:
     2.5-class models think with an automatic budget, and a 24-shot board spent
     133 SECONDS doing it (28s at 1024). ⚠ The bounds matter in BOTH directions —
     below ~1024 the polish call comes back with an empty plan, and at 0 it loses
     the thread and generates until the output cap stops it.
  6. The Google adapter converts `thinking_tokens` into the `ThinkingConfig` the
     SDK wants, passes the cap through, and never leaks the raw key — checked
     against a fake client, so it is the real code path with no network.

No network, no model, no key: `use_adapter` swaps the transport for a function
that counts calls and burns clock.
"""

import io
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import llm_json  # noqa: E402
from llm_json import JsonRequest, LLMJsonError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


REQUEST = JsonRequest(
    purpose="analyse",
    system="you are a test",
    prompt="say something",
    schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
)


class _Capture(logging.Handler):
    """Every line `llm_json` logged, already %-formatted."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())

    def __enter__(self):
        self.previous = llm_json.logger.level
        llm_json.logger.setLevel(logging.DEBUG)
        llm_json.logger.addHandler(self)
        return self

    def __exit__(self, *_):
        llm_json.logger.removeHandler(self)
        llm_json.logger.setLevel(self.previous)
        return False

    def find(self, *needles: str) -> str:
        """The first line carrying all of `needles`, or ""."""
        for line in self.lines:
            if all(n in line for n in needles):
                return line
        return ""


def main():
    # A clock nobody set: the ceiling, straight off the env default.
    os.environ.pop("DIRECTOR_TIMEOUT_SECONDS", None)
    os.environ.pop("DIRECTOR_BUDGET_SECONDS", None)

    print()
    print("⚠ ONE ATTEMPT'S CEILING, AND WHAT IS LEFT OF THE CALL'S BUDGET")
    print()
    check("with no deadline set, an attempt gets the ceiling",
          llm_json.call_timeout() == llm_json.DEFAULT_TIMEOUT_SECONDS,
          str(llm_json.call_timeout()))

    token = llm_json._deadline.set(time.monotonic() + 40)
    try:
        got = llm_json.call_timeout()
        check("⚠ WITH ONE SET, IT IS THE REMAINING BUDGET — not the ceiling, which\n"
              "       is how a 180s attempt used to be started with 20s left to run",
              35 <= got <= 40, str(got))
    finally:
        llm_json._deadline.reset(token)

    token = llm_json._deadline.set(time.monotonic() + 1)
    try:
        got = llm_json.call_timeout()
        check("...and never less than the floor, because a 1s timeout fails every"
              " call it is given to",
              got == llm_json.MIN_ATTEMPT_SECONDS, str(got))
    finally:
        llm_json._deadline.reset(token)

    print()
    print("⚠ A CALL WHOSE BUDGET IS SPENT STOPS, RATHER THAN SLEEPING INTO A")
    print("  BROWSER THAT HAS ALREADY GIVEN UP")
    print()

    # A transport that burns most of the budget and then answers unusably. The
    # first attempt alone must leave too little for a second.
    calls = {"n": 0}

    def slow_and_broken(request):
        calls["n"] += 1
        time.sleep(1.2)
        return "this is not json"

    os.environ["DIRECTOR_BUDGET_SECONDS"] = "1.5"
    llm_json.use_adapter(slow_and_broken)
    try:
        started = time.monotonic()
        try:
            llm_json.complete_json(REQUEST)
            reason = ""
        except LLMJsonError as e:
            reason = str(e)
        took = time.monotonic() - started
    finally:
        llm_json.use_adapter(None)
        os.environ.pop("DIRECTOR_BUDGET_SECONDS", None)

    check("it gives up rather than running the full three attempts",
          calls["n"] <= 2, f"{calls['n']} attempts")
    check("...quickly — no 4s backoff into a budget that is already gone",
          took < 4, f"{took:.1f}s")
    check("⚠ AND THE REASON NAMES THE CLOCK, so 'the model is slow' and\n"
          "       'something is stuck' are told apart in the panel",
          "ran out of time" in reason, reason)
    check("...and still says what actually went wrong first",
          "unusable JSON" in reason or "could not be read" in reason, reason)

    print()
    print("⚠ AND A FAILURE THAT IS *NOT* THE CLOCK IS NOT REPORTED AS ONE")
    print()

    def broken(request):
        return "this is not json either"

    llm_json.use_adapter(broken)
    os.environ["DIRECTOR_BUDGET_SECONDS"] = "600"
    try:
        try:
            llm_json.complete_json(REQUEST)
            reason = ""
        except LLMJsonError as e:
            reason = str(e)
    finally:
        llm_json.use_adapter(None)
        os.environ.pop("DIRECTOR_BUDGET_SECONDS", None)
    check("a model that answers instantly with rubbish is a JSON fault, not a timeout",
          "unusable JSON" in reason and "ran out of time" not in reason, reason)

    print()
    print("⚠ THE SERVER'S BUDGET AND THE BROWSER'S PATIENCE STILL AGREE")
    print()
    api = io.open(ROOT / "client/src/api.js", encoding="utf-8").read()
    found = re.search(r"const PLAN_TIMEOUT_MS = (\d+);", api)
    waits = int(found.group(1)) / 1000 if found else 0
    check("client/src/api.js gives the plan route its own, longer timeout",
          waits > 0, "PLAN_TIMEOUT_MS not found")
    # The plan route is analyse + polish: two whole calls, each with its own budget.
    check("⚠ AND IT IS LONGER THAN THE TWO CALLS THAT ROUTE MAKES — otherwise the\n"
          "       tab aborts a request the server is still correctly serving, and the\n"
          "       paid call in flight is billed anyway",
          waits > 2 * llm_json.DEFAULT_BUDGET_SECONDS,
          f"waits {waits:.0f}s, server may take {2 * llm_json.DEFAULT_BUDGET_SECONDS:.0f}s")

    print()
    print("⚠ THE GOOGLE ADAPTER ASKS FOR A TIMEOUT AT ALL — it is the DEFAULT")
    print("  provider and it used to pass none, which is the hang itself")
    print()
    src = io.open(ROOT / "llm_json.py", encoding="utf-8").read()
    google = src[src.index("def _google_adapter"):src.index("def _openai_adapter")]
    check("`_google_adapter` sets http_options from the clock",
          "http_options" in google and "call_timeout()" in google)
    check("...in MILLISECONDS, which is what `HttpOptions.timeout` is documented in",
          "call_timeout() * 1000" in google, google[google.find("http_options"):][:120])
    # ⚠ `def _adapter(` WITHOUT THE CLOSING PAREN. `_adapter` grew a `capability`
    # argument when the ✨ chat was given its own provider, and pinning the empty
    # signature here failed this file with a ValueError from `str.index` — a
    # crash about a substring, in a file about timeouts.
    openai = src[src.index("def _openai_adapter"):src.index("def _adapter(")]
    check("...and the OpenAI adapter reads the same clock rather than the raw env",
          "timeout=call_timeout()" in openai)

    print()
    print("⚠ THE TWO KNOBS THAT DECIDE HOW LONG A PLAN TAKES — they were BOTH")
    print("  unset, which is why a 24-shot board took 133s and an 8-shot one 504'd")
    print()
    s = llm_json.sampling()
    check("the thinking budget travels in `sampling()`, so it is in the fingerprint",
          "thinking_tokens" in s, json.dumps(s))
    check("...and so does the output cap", "max_output_tokens" in s, json.dumps(s))
    check("⚠ THE THINKING BUDGET IS NOT ZERO — with no thinking at all the polish\n"
          "       call loses the thread and generates until something stops it",
          s["thinking_tokens"] > 0, json.dumps(s))
    check("⚠ ...NOR BELOW 1024, which is where the polish call started coming back\n"
          "       with an empty plan on a 24-shot board: fast, and worthless",
          s["thinking_tokens"] >= 1024, json.dumps(s))
    check("...and the cap is roomy enough for a big board (~70 tokens a step)",
          s["max_output_tokens"] >= 8192, json.dumps(s))
    check("greedy decoding still holds — none of this made the plan random",
          llm_json.is_greedy(), json.dumps(s))

    print()
    print("⚠ AND THE GOOGLE ADAPTER TURNS THEM INTO WHAT THE SDK WANTS")
    print()

    seen = {}

    class _FakeModels:
        def generate_content(self, model, contents, config):
            seen["config"] = config
            seen["model"] = model

            class R:
                text = '{"ok": true}'
                candidates = []

            return R()

    class _FakeClient:
        models = _FakeModels()

    import script_breakdown

    real = script_breakdown.get_client
    # ⚠ THE FAKE HAS TO ACCEPT `key_env` TOO. The real `get_client` takes a
    # capability's own key env var since the ✨ chat was given its own; a stand-in
    # that refuses the keyword fails this file with a TypeError about an argument
    # rather than anything to do with a timeout.
    script_breakdown.get_client = lambda provider=None, *, key_env="": _FakeClient()
    os.environ["DIRECTOR_PROVIDER"] = "vertex"
    try:
        llm_json._google_adapter(REQUEST)
    except Exception as e:  # noqa: BLE001
        print("    adapter raised:", type(e).__name__, str(e)[:200])
    finally:
        script_breakdown.get_client = real
        os.environ.pop("DIRECTOR_PROVIDER", None)

    cfg = seen.get("config")
    check("the adapter was reached and handed a config", cfg is not None)
    if cfg is not None:
        budget = getattr(getattr(cfg, "thinking_config", None), "thinking_budget", None)
        check("⚠ `thinking_tokens` BECOMES A `ThinkingConfig`, at the value asked for",
              budget == s["thinking_tokens"], f"thinking_budget={budget}")
        check("...and the raw key never reaches the SDK as a field of its own",
              not hasattr(cfg, "thinking_tokens"))
        check("the output cap is passed through as the config field it already is",
              getattr(cfg, "max_output_tokens", None) == s["max_output_tokens"],
              str(getattr(cfg, "max_output_tokens", None)))
        ms = getattr(getattr(cfg, "http_options", None), "timeout", None)
        check("...and the http timeout is set, in milliseconds",
              isinstance(ms, int) and ms > 1000, str(ms))
    # ---------------------------------------------------------------------
    print()
    print("⚠ AND THE CHAT'S BUDGET IS NOT THE DIRECTOR'S — it CANNOT be, because")
    print("  the tab waiting for a chat turn waits far less, and the Director's waits 300")
    print()
    os.environ.pop("DIRECTOR_BUDGET_SECONDS", None)
    os.environ.pop("CHAT_BUDGET_SECONDS", None)

    plan_budget, plan_env = llm_json.budget_seconds("")
    chat_budget, chat_env = llm_json.budget_seconds("chat")
    check("the Director's call still gets the shared default",
          plan_budget == llm_json.DEFAULT_BUDGET_SECONDS, str(plan_budget))
    check("...named as the var an operator would raise",
          plan_env == "DIRECTOR_BUDGET_SECONDS", plan_env)
    check("a chat turn gets its own, and a SMALLER one",
          chat_budget < plan_budget, f"{chat_budget} vs {plan_budget}")

    # THE WHOLE POINT. `CHAT_TURN_TIMEOUT_MS` in `client/src/api.js` is what the
    # browser will sit through; a server allowed longer than that spends the
    # money, finishes the turn, and hands the answer to a tab that stopped
    # listening — which is the bug this section exists to keep fixed.
    api_js = (ROOT / "client" / "src" / "api.js").read_text(encoding="utf-8")
    m = re.search(r"const CHAT_TURN_TIMEOUT_MS = (\d+)", api_js)
    tab_s = int(m.group(1)) / 1000 if m else 0
    check("the browser's chat timeout is still findable in api.js", bool(m), "regex missed")
    check("⚠ THE SERVER GIVES UP BEFORE THE BROWSER DOES, with room to spare",
          bool(m) and chat_budget + 15 <= tab_s, f"server {chat_budget}s vs tab {tab_s}s")

    m = re.search(r'CHAT_TURN_TIMEOUT_S = float\(os\.environ\.get\("API_CHAT_TURN_TIMEOUT_S", "(\d+)"\)\)',
                  (ROOT / "server" / "config.py").read_text(encoding="utf-8"))
    check("...and server/config.py still mirrors the same number as the tab",
          bool(m) and float(m.group(1)) == tab_s,
          f"config {m.group(1) if m else '?'} vs tab {tab_s}")

    os.environ["DIRECTOR_BUDGET_SECONDS"] = "300"
    check("⚠ RAISING THE DIRECTOR'S DOES NOT DRAG THE CHAT PAST THE TAB",
          llm_json.budget_seconds("chat")[0] == chat_budget,
          str(llm_json.budget_seconds("chat")))
    os.environ["DIRECTOR_BUDGET_SECONDS"] = "40"
    check("...but a SHORTER shared budget still applies — the cap is a ceiling",
          llm_json.budget_seconds("chat")[0] == 40.0,
          str(llm_json.budget_seconds("chat")))
    os.environ["CHAT_BUDGET_SECONDS"] = "200"
    check("...and CHAT_BUDGET_SECONDS is the one way to say 'let it run longer'",
          llm_json.budget_seconds("chat") == (200.0, "CHAT_BUDGET_SECONDS"),
          str(llm_json.budget_seconds("chat")))
    os.environ.pop("DIRECTOR_BUDGET_SECONDS", None)
    os.environ.pop("CHAT_BUDGET_SECONDS", None)

    # ⚠ THE SENTENCE IS ONLY STAMPED WHEN THE CLOCK IS WHY WE STOPPED, so the
    # clock has to be genuinely spent for this to say anything. Set it in the
    # past and read what a chat turn would actually print.
    spent = llm_json._deadline.set(time.monotonic() - 1)
    try:
        said = llm_json._with_clock("It failed.", 70, "CHAT_BUDGET_SECONDS")
    finally:
        llm_json._deadline.reset(spent)
    check("⚠ the sentence names the var that actually set the clock",
          "CHAT_BUDGET_SECONDS" in said and "DIRECTOR_BUDGET_SECONDS" not in said, said)

    # ---------------------------------------------------------------------
    # ⚠ THE ORDER BEING RIGHT IS NOT THE SAME AS THE NUMBER BEING BIG ENOUGH,
    #   and that is a SECOND fault, found the same way as the first — live,
    #   with a screenshot. Everything above this line only asserts that the
    #   server gives up BEFORE the tab does. It was perfectly satisfied by
    #   70 < 90, and 70 was too small to do the work: "add music and sound
    #   effects in this storyboard story wise" on a FOURTEEN-shot board came
    #   back as
    #
    #       "The read operation timed out. It ran out of time — 70s is all one
    #        call gets (CHAT_BUDGET_SECONDS)."
    #
    #   ⚠ SO THE BUDGET IS CHECKED AGAINST MEASURED WORK, NOT AGAINST ITSELF.
    #   Both shapes of turn, because they fail in opposite directions: a
    #   budget too small kills the SLOW turn, and a budget spent on one
    #   hopeless attempt kills the FAST one's retries. The seconds below are
    #   measured, not guessed — six live calls on 2026-09-04,
    #   `gemini-3.5-flash` on the Developer API, written up in AGENTS.md.
    # ---------------------------------------------------------------------
    print()
    print("⚠ TWO SHAPES OF TURN, AND ONE BUDGET THAT HAS TO HOLD BOTH")
    print()
    os.environ.pop("DIRECTOR_BUDGET_SECONDS", None)
    os.environ.pop("CHAT_BUDGET_SECONDS", None)
    chat_budget, _ = llm_json.budget_seconds("chat")

    # Measured, live, on the same 9-shot board seconds apart:
    #   text turn        2.6s, 5.2s, 4.2s   → the FAST shape
    #   look, 5 stills   34.6s              → the SLOW shape
    #   look, 12 stills  27.1s              (more pictures is not more seconds)
    FAST_TURN_S = 5.0
    SLOWEST_MEASURED_CALL_S = 35.0

    # ⚠ THE BIG TURN IS TWO CALLS INSIDE ONE ATTEMPT, NOT ONE. A repair —
    # asking the model to mend its own unreadable JSON — is a second paid round
    # trip that `_attempts` makes before it has spent a single retry, so the
    # slowest attempt this module can produce is the slowest call TWICE. A
    # budget that fits only one of them turns every malformed answer into a
    # timeout the user reads as the app being broken.
    slow_attempt = 2 * SLOWEST_MEASURED_CALL_S
    check("⚠ THE BIG TURN FITS: the slowest measured call PLUS its repair",
          chat_budget >= slow_attempt,
          f"budget {chat_budget}s vs {slow_attempt}s (look {SLOWEST_MEASURED_CALL_S}s x 2)")
    # ⚠ AND THE OLD NUMBER MISSED BY NOTHING AT ALL, which is the shape of
    # this bug: 35 × 2 is 70 EXACTLY, so a slow look whose answer needed
    # mending had not one second of room to mend it in. A budget that a
    # measured attempt only just fits is a budget that fails.
    check("...and the old 70s left NOT ONE SECOND for it — the bug this moved",
          70.0 <= slow_attempt, f"70s vs {slow_attempt}s")

    # ⚠ AND THE HTTP TIMEOUT HANDED TO THE SDK IS THE WHOLE BUDGET, not the
    # ceiling. `call_timeout()` returns the SMALLER of `DIRECTOR_TIMEOUT_SECONDS`
    # and what is left, so raising the budget buys nothing if the ceiling sits
    # underneath it — the socket would cut the call off early and the sentence
    # the user reads would still name the budget, which is the wrong knob.
    os.environ.pop("DIRECTOR_TIMEOUT_SECONDS", None)
    fresh = llm_json._deadline.set(time.monotonic() + chat_budget)
    try:
        first = llm_json.call_timeout()
    finally:
        llm_json._deadline.reset(fresh)
    check("...and the first attempt may really use all of it (the ceiling is above)",
          first >= chat_budget - 1, f"{first:.1f}s of {chat_budget}s")

    # ⚠ THE SMALL TURN IS THE OPPOSITE RISK. `_worth_retrying` estimates the
    # next attempt from what the LAST one took, so the budget must still leave
    # room for a FAST failure to be tried again — a 503 that comes back in two
    # seconds is the case retries exist for, and it must not be starved by a
    # number chosen for the slow shape. Simulated by moving the deadline, so
    # the rule under test is the real one and no model is called.
    def afford(attempt, spent_so_far, last):
        token = llm_json._deadline.set(time.monotonic() + chat_budget - spent_so_far)
        try:
            return llm_json._worth_retrying(attempt, REQUEST, chat_budget, last)
        finally:
            llm_json._deadline.reset(token)

    check("⚠ THE SMALL TURN KEEPS ITS RETRIES: a fast failure is tried again",
          afford(1, FAST_TURN_S, FAST_TURN_S), f"after {FAST_TURN_S}s of {chat_budget}s")
    check("...and again after the backoff, on the last attempt it has",
          afford(2, 2 * FAST_TURN_S + 4, FAST_TURN_S),
          f"after {2 * FAST_TURN_S + 4}s of {chat_budget}s")
    check("...but a SLOW failure with no room left is still refused, not bought",
          not afford(1, chat_budget - 10, SLOWEST_MEASURED_CALL_S),
          f"10s left, needs {SLOWEST_MEASURED_CALL_S}s")

    # ⚠ AND THE TAB HAS TO OUTLAST THE BIGGEST TURN THE SERVER WILL SERVE — the
    # whole budget plus the wire, not the model's seconds alone.
    check("⚠ AND THE TAB STILL OUTLASTS IT, with the wire's share on top",
          chat_budget + 15 <= tab_s, f"server {chat_budget}s vs tab {tab_s}s")

    # ⚠ THE TRAP THE NEXT RAISE WALKS INTO, PINNED. `budget_seconds` hands back
    # the SMALLER of the chat's ceiling and `DIRECTOR_BUDGET_SECONDS`, so a
    # ceiling pushed above the Director's does not raise the chat at all — it
    # silently hands the turn the DIRECTOR'S number under the DIRECTOR'S name,
    # and the sentence the user reads then names a var that changes nothing for
    # them. Whoever raises 120 next has to raise the Director's with it, or set
    # CHAT_BUDGET_SECONDS, which overrides both.
    check("⚠ THE CEILING STAYS UNDER THE DIRECTOR'S, OR IT STOPS BEING THE CHAT'S",
          chat_budget < llm_json.DEFAULT_BUDGET_SECONDS,
          f"chat {chat_budget}s vs shared {llm_json.DEFAULT_BUDGET_SECONDS}s")
    was = llm_json.CAPABILITY_BUDGET_SECONDS["chat"]
    llm_json.CAPABILITY_BUDGET_SECONDS["chat"] = llm_json.DEFAULT_BUDGET_SECONDS + 30
    try:
        over = llm_json.budget_seconds("chat")
    finally:
        llm_json.CAPABILITY_BUDGET_SECONDS["chat"] = was
    check("...proved: a ceiling over the shared budget yields the Director's, by name",
          over == (llm_json.DEFAULT_BUDGET_SECONDS, "DIRECTOR_BUDGET_SECONDS"), str(over))

    # ---------------------------------------------------------------------
    # ⚠ AND A SLOW CALL HAS TO BE READABLE OFF THE LOG — the open question
    #   that the two sections above could NOT answer.
    #
    # The budgets are honest and the tab has a counter, and neither of those
    # says WHY a turn took 70 seconds. "One slow model call", "three attempts",
    # and "one fast call plus a repair" are three different faults with three
    # different fixes, and they used to produce the same log: the START of an
    # attempt, and nothing else. A turn that SUCCEEDED slowly logged no duration
    # at all — and those are the turns people actually complain about.
    # ---------------------------------------------------------------------
    print()
    print("⚠ THE LOG SAYS HOW LONG, NOT JUST THAT IT STARTED")
    print()
    os.environ["DIRECTOR_BUDGET_SECONDS"] = "600"

    def slow_but_fine(request):
        time.sleep(0.35)
        return '{"ok": true}'

    llm_json.use_adapter(slow_but_fine)
    try:
        with _Capture() as log:
            llm_json.complete_json(REQUEST)
    finally:
        llm_json.use_adapter(None)

    answered = log.find("attempt 1/3", "the model answered in")
    check("⚠ A CALL THAT WORKED STILL SAYS HOW LONG THE MODEL TOOK",
          bool(answered), " | ".join(log.lines) or "no lines")
    m = re.search(r"answered in ([\d.]+)s", answered)
    check("...and it is the real elapsed time, not a constant",
          bool(m) and 0.3 <= float(m.group(1)) < 5, answered)

    done = log.find("DONE in")
    check("⚠ AND THE WHOLE CALL IS REPORTED, RETRIES AND BACKOFF INCLUDED —\n"
          "       this is the number the user actually sat through",
          bool(done), " | ".join(log.lines) or "no lines")
    check("...naming how many ATTEMPTS it took", "1 attempt(s)" in done, done)
    check("⚠ ...AND HOW MANY MODEL CALLS, which is NOT the same number",
          "1 model call(s)" in done, done)
    check("...and the budget it was measured against, by the var that set it",
          "600s budget" in done and "DIRECTOR_BUDGET_SECONDS" in done, done)

    started_line = log.find("attempt 1/3", "provider=")
    check("⚠ THE SIZES GO OUT WITH THE ATTEMPT, so 'is the prompt the suspect?'\n"
          "       is answered by the log rather than measured by hand once",
          all(k in started_line for k in ("system=", "prompt=", "schema=")), started_line)
    check("...and pictures are named ONLY when there are some — `images=0` on\n"
          "       every Director line is noise, `images=5` is the finding (E112)",
          "images=" not in started_line, started_line)

    shape = llm_json._shape(JsonRequest(
        purpose="editor chat", system="s", prompt="p", schema={},
        images=({"mime": "image/png", "data": b"x" * 2048},),
    ))
    check("⚠ ...and a LOOK says how many and how heavy, on the one line that has them",
          "images=1" in shape and "2.0KB" in shape, shape)

    # ⚠ THE HIDDEN DOUBLE. A repair is a second paid round trip INSIDE attempt 1,
    # so a turn can be two model calls while the log says "attempt 1/3" — which
    # is exactly the reading that would send somebody looking for a slow model
    # when what they have is two ordinary ones.
    tries = {"n": 0}

    def broken_then_mended(request):
        tries["n"] += 1
        return '{"ok": true}' if tries["n"] > 1 else "not json at all"

    llm_json.use_adapter(broken_then_mended)
    try:
        with _Capture() as log:
            llm_json.complete_json(REQUEST)
    finally:
        llm_json.use_adapter(None)
        os.environ.pop("DIRECTOR_BUDGET_SECONDS", None)

    check("⚠ A REPAIR IS TIMED SEPARATELY — it is a SECOND paid call, not a detail",
          bool(log.find("repair call took")), " | ".join(log.lines) or "no lines")
    done = log.find("DONE in")
    check("⚠ ...AND THE TALLY SHOWS IT: one attempt, TWO model calls",
          "1 attempt(s)" in done and "2 model call(s)" in done, done)

    # ⚠ AND A FAILURE SAYS HOW LONG IT TOOK TO FAIL. A 429 in 0.4s is a quota
    # wall; the same sentence after 60s is the SDK having retried it for us, and
    # "raise the budget" is the wrong fix for the second one.
    def always_429(request):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    llm_json.use_adapter(always_429)
    os.environ["DIRECTOR_BUDGET_SECONDS"] = "1.5"
    try:
        with _Capture() as log:
            try:
                llm_json.complete_json(REQUEST)
            except LLMJsonError:
                pass
    finally:
        llm_json.use_adapter(None)
        os.environ.pop("DIRECTOR_BUDGET_SECONDS", None)
    check("⚠ A FAILED ATTEMPT SAYS HOW LONG IT TOOK TO FAIL",
          bool(log.find("attempt 1/3 FAILED after")), " | ".join(log.lines) or "no lines")

    # ---------------------------------------------------------------------
    # And the route the user is actually waiting on times ITSELF, because
    # `llm_json`'s number is the model's and the tab's is the whole request.
    # ---------------------------------------------------------------------
    chat_py = (ROOT / "server" / "editor_chat.py").read_text(encoding="utf-8")
    check("⚠ THE CHAT ROUTE TIMES THE WHOLE TURN, not only the model call —\n"
          "       when the two numbers differ, this app is the story, not the model",
          "turn_started = time.monotonic()" in chat_py)
    check("...and prints it on the line it already logs",
          re.search(r"\[editor-chat %s\] %s in %\.1fs", chat_py) is not None)
    check("⚠ ...AND SAYS WHETHER THE TURN WAS LOOKING, so the slow turns and the\n"
          "       seeing turns can be correlated at all (E112)",
          "looking=%d" in chat_py)
    check("...and a turn that FAILED reports its duration too",
          "turn failed after %.1fs" in chat_py)

    # ---------------------------------------------------------------------
    # ⚠ WHAT THE STOPWATCH FOUND, AND THE TWO FAULTS IT MADE FIXABLE.
    #
    # Measured live on 2026-09-04 against `gemini-3.5-flash` (Developer API),
    # the same board and the same turn, seconds apart:
    #
    #     text only .................  2.6s
    #     with 5 stills (a LOOK) .... 34.6s
    #     with 12 stills ............ 27.1s
    #     three consecutive 503s .... 1.8-2.5s each
    #
    # So a plain turn was never the 90s anybody reported; a LOOK is ~10x a text
    # turn, and a look that meets one transient fault is 35 + backoff + 35 —
    # which lands exactly where the complaints did.
    # ---------------------------------------------------------------------
    print()
    print("⚠ A RETRY THAT CANNOT FINISH IS NOT ATTEMPTED — the estimate is what")
    print("  the LAST attempt measured, not a flat fifteen seconds")
    print()

    # 35s of budget gone, 35s left, and the attempt that just failed took 34s.
    # The floor alone would say "15s is enough, go again" and buy a call that
    # gets cut off; the measurement says it cannot land.
    token = llm_json._deadline.set(time.monotonic() + 35)
    try:
        check("⚠ AN ATTEMPT THAT JUST TOOK 34s IS NOT RETRIED INTO 35s",
              llm_json._worth_retrying(1, REQUEST, 70, 34.0) is False)
        check("...but a FAST failure still gets its retry — the floor is a floor,\n"
              "       so a 2s 503 is not punished for the slow call's sins",
              llm_json._worth_retrying(1, REQUEST, 70, 2.0) is True)
        check("...and the old flat-floor behaviour is what 'no measurement' means",
              llm_json._worth_retrying(1, REQUEST, 70) is True)
    finally:
        llm_json._deadline.reset(token)

    # ⚠ AND THE SENTENCE HAS TO SAY THE CLOCK STOPPED IT, even though the clock
    # has time on it — the ending `_time_left()` alone cannot recognise.
    token = llm_json._deadline.set(time.monotonic() + 31)
    try:
        said = llm_json._with_clock("It failed.", 70, "CHAT_BUDGET_SECONDS", True)
        quiet = llm_json._with_clock("It failed.", 70, "CHAT_BUDGET_SECONDS", False)
    finally:
        llm_json._deadline.reset(token)
    check("⚠ A CALL ABANDONED WITH TIME ON THE CLOCK STILL BLAMES THE CLOCK",
          "ran out of time" in said, said)
    check("...and a plain failure with time left still does NOT",
          "ran out of time" not in quiet, quiet)

    # ---------------------------------------------------------------------
    # ⭐ A REBUILT REQUEST MUST NOT CHANGE WHO IS CALLED.
    #
    # Two functions here hand back a NEW `JsonRequest` built from an old one —
    # `as_prompt_schema` (the schema moved into the prompt) and
    # `_repair_request` (mend your own broken answer). Both were rebuilding it
    # WITHOUT `capability`, and an empty capability is not a small thing: it
    # means the DEFAULT provider. `resolve_provider(capability="")` falls back
    # to `TEXT_PROVIDER`, so on this repo's own `.env` — chat on the Developer
    # API, text on Vertex — a repair of a ✨ chat answer went to Vertex, with no
    # credentials for it, and came back:
    #
    #     403 PERMISSION_DENIED … aiplatform.googleapis.com … CONSUMER_INVALID
    #
    # ⚠ AN ERROR ABOUT CREDENTIALS, ON A CALL WHOSE ONLY FAULT WAS A MISSING
    # BRACE, ON A KEY THAT WAS PERFECTLY GOOD. Seen live on 2026-09-05 while
    # running the chat battery. ⚠ AND WHERE BOTH BACKENDS HAVE CREDENTIALS IT
    # DOES NOT FAIL AT ALL — it silently bills the mend to the other
    # capability's key, which is the exact bug `get_client`'s cache key exists
    # to prevent. A wrong answer that works is worse than one that 403s.
    # ---------------------------------------------------------------------
    print()
    print("⭐ A REBUILT REQUEST KEEPS ITS CAPABILITY — or it changes provider")
    print()

    looking = JsonRequest(
        purpose="editor chat", system="s", prompt="p",
        schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        capability="chat",
        images=({"mime": "image/png", "data": b"xx"},),
    )

    moved = llm_json.as_prompt_schema(looking)
    check("⭐ as_prompt_schema KEEPS the capability",
          moved.capability == "chat", repr(moved.capability))
    check("...and the pictures, because it is the SAME call",
          len(moved.images) == 1, str(len(moved.images)))

    mend = llm_json._repair_request(looking, '{"ok": tru', "unterminated")
    check("⭐ _repair_request KEEPS the capability",
          mend.capability == "chat", repr(mend.capability))
    check("...and DELIBERATELY drops the pictures — a mend is a syntax job",
          mend.images == (), str(mend.images))
    check("...and keeps the purpose, which is what `stub` answers by",
          mend.purpose == "editor chat", mend.purpose)

    # ⚠ AND THE PROOF THAT MATTERS IS THE ROUND TRIP, not the two fields above:
    # the adapter is what asks `resolve_provider`, so the question is what the
    # adapter is HANDED on the second call of a repair. Answered here with a
    # fake transport, no model and no network — first answer unreadable, so a
    # repair is bought; both requests are recorded.
    os.environ["DIRECTOR_BUDGET_SECONDS"] = "600"
    seen: list = []

    def breaks_once(request):
        seen.append(request.capability)
        return '{"ok": tru' if len(seen) == 1 else '{"ok": true}'

    llm_json.use_adapter(breaks_once)
    try:
        llm_json.complete_json(JsonRequest(
            purpose="editor chat", system="s", prompt="p",
            schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
            capability="chat",
        ))
    finally:
        llm_json.use_adapter(None)
        os.environ.pop("DIRECTOR_BUDGET_SECONDS", None)

    check("⭐ THE REPAIR IS A SECOND CALL — that is the whole risk",
          len(seen) == 2, str(seen))
    check("⭐ ...AND BOTH CALLS ARE THE SAME CAPABILITY, so both reach the same",
          seen == ["chat", "chat"], str(seen))
    check("...which is what stops the mend resolving to TEXT_PROVIDER",
          all(c for c in seen), str(seen))

    print()
    print("⚠ AND A PROVIDER FAULT REACHES THE USER AS A SENTENCE, NOT A DICT")
    print()

    # The exact string Google returned on 2026-09-04, three times running.
    live_503 = ("503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is "
                "currently experiencing high demand. Spikes in demand are usually "
                "temporary. Please try again later.', 'status': 'UNAVAILABLE'}}")
    said = llm_json._explain(live_503, "editor chat")
    check("⚠ A BUSY MODEL IS EXPLAINED, AND THE BRACES NEVER REACH THE PANEL",
          "busy" in said and "{" not in said, said)
    check("...and it says the thing the reader needs: it is not their fault",
          "try again" in said, said)
    check("⚠ ...AND IT IS NOT CONFUSED WITH A QUOTA WALL, which needs a different\n"
          "       action from the reader entirely",
          "quota" not in said, said)

    quota = llm_json._explain("429 RESOURCE_EXHAUSTED quota", "editor chat")
    check("a 429 still reads as rate limiting", "rate limited" in quota.lower(), quota)
    check("...and names the free key, which is the usual cause", "free key" in quota, quota)
    check("a bad key is its own sentence",
          "credentials" in llm_json._explain("403 PERMISSION_DENIED", "x"))
    check("a wrong model id is its own sentence",
          "model id" in llm_json._explain("404 NOT_FOUND models/nope", "x"))
    check("a 504 is named as the call being cut off",
          "cut off" in llm_json._explain("504 DEADLINE_EXCEEDED", "x"))

    # ⚠ AND AN UNRECOGNISED FAULT IS STILL TRIMMED. The rule is not "know every
    # error", it is "never paste an object into a conversation".
    messy = llm_json._explain("weird failure\n  File x.py line 3\n  boom" + "z" * 400, "editor chat")
    check("⚠ AN UNKNOWN FAULT IS CLIPPED TO ONE LINE, NOT PASTED WHOLE",
          "\n" not in messy and len(messy) < 220, f"{len(messy)} chars: {messy[:80]}")

    # And the whole path, through the real retry loop with a fake transport.
    def busy(request):
        raise RuntimeError(live_503)

    llm_json.use_adapter(busy)
    os.environ["DIRECTOR_BUDGET_SECONDS"] = "600"
    try:
        with _Capture() as log:
            try:
                llm_json.complete_json(REQUEST)
                reason = ""
            except LLMJsonError as e:
                reason = str(e)
    finally:
        llm_json.use_adapter(None)
        os.environ.pop("DIRECTOR_BUDGET_SECONDS", None)
    check("⚠ END TO END: the reason a caller raises carries no dict either",
          "busy" in reason and "{" not in reason, reason)
    check("...and the raw provider text is kept, in the LOG where it belongs",
          bool(log.find("raw:", "UNAVAILABLE")), " | ".join(log.lines)[:300] or "no lines")

    print()
    if failures:
        print(f"{len(failures)} FAILED:")
        for name in failures:
            print("  -", name)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
