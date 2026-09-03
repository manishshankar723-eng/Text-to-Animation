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
