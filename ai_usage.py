"""
ai_usage.py — what one AI text call actually cost, in tokens.

Until now nothing in this app counted tokens. Image and Veo spend were visible
(`cost_usd` on a render), but the TEXT calls — the breakdown, the planner, the
Director — were invisible: you could hold a forty-turn conversation with the
planning agent and have no idea whether that was a rounding error or real money.

So every text call now reports what it used, and the number travels with the
thing it produced rather than only into a log line nobody reads.

THE SHAPE, and why it is this shape:

    Usage(input, output, thinking, cached, total, calls)

`thinking` is broken out because 2.5-class models bill reasoning tokens as
OUTPUT while reporting them separately, and a plan that looks expensive is
usually expensive there — see DIRECTOR_THINKING_TOKENS in AGENTS.md, which is
the same discovery from the latency side. Lumping them in hides the one knob
that would fix it.

`calls` is here because one user action is often several API calls (the planner
retries; the Director makes two). "That plan cost 12k tokens across 2 calls" is
the honest sentence; "that plan cost 12k tokens" invites the reader to divide by
one and get the wrong per-call figure.

⚠ **THE DOLLAR FIGURE IS ADVISORY AND SAYS SO.** Only Google bills. List prices
drift, free-tier quota is not modelled, and `gemini-2.5-flash` is a rolling
alias whose price can move under a fixed model id. It exists for the same reason
`VeoQuote` does — so a number appears BEFORE the click rather than on an invoice
four weeks later — and every surface that shows it must call it an estimate.
Override any of it with AI_PRICE_<MODEL> env vars rather than editing the table.
"""

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prices, USD per 1,000,000 tokens
# ---------------------------------------------------------------------------
# Keyed by model id PREFIX so a dated snapshot (`gemini-2.5-flash-preview-…`)
# prices the same as the rolling alias it was cut from. Longest prefix wins, so
# `flash-lite` is never priced as `flash`.
#
# Thinking tokens bill at the OUTPUT rate — they are output, the API just
# reports them on their own line.
_PRICES = {
    "gemini-2.5-pro":         (1.25, 10.00),
    "gemini-2.5-flash-lite":  (0.10, 0.40),
    "gemini-2.5-flash":       (0.30, 2.50),
    "gemini-2.0-flash-lite":  (0.075, 0.30),
    "gemini-2.0-flash":       (0.10, 0.40),
    "gemini-1.5-pro":         (1.25, 5.00),
    "gemini-1.5-flash":       (0.075, 0.30),
}
# Charged when a prompt is served from cache instead of re-read. We do not use
# explicit caching yet, but the field is reported, so price it rather than
# silently counting cached tokens at the full input rate.
_CACHED_DISCOUNT = 0.25

_UNKNOWN_MODEL_WARNED: set[str] = set()


def _price_for(model_id: str) -> tuple[float, float] | None:
    """(input, output) USD per 1M tokens for `model_id`, or None if unpriced.

    An env override wins: AI_PRICE_GEMINI_2_5_FLASH="0.30,2.50". Set that rather
    than editing the table, so a price correction doesn't need a deploy.
    """
    key = (model_id or "").strip().lower()
    if not key:
        return None

    env_name = "AI_PRICE_" + "".join(c if c.isalnum() else "_" for c in key).upper()
    raw = (os.environ.get(env_name) or "").strip()
    if raw:
        try:
            i, o = (float(p) for p in raw.split(",", 1))
            return (i, o)
        except ValueError:
            logger.warning("[usage] %s=%r is not 'input,output' — ignoring.", env_name, raw)

    # Longest matching prefix, so flash-lite beats flash.
    best = None
    for prefix, price in _PRICES.items():
        if key.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, price)
    if best:
        return best[1]

    # Warn ONCE per model. An unpriced model must not spam a log line per call,
    # and it must not be silently priced as something else either.
    if key not in _UNKNOWN_MODEL_WARNED:
        _UNKNOWN_MODEL_WARNED.add(key)
        logger.info("[usage] no price known for model %r — tokens counted, cost omitted.", key)
    return None


@dataclass
class Usage:
    """Tokens used by one or more text calls.

    Additive: `a + b` is the two calls together, which is what makes a session
    total the sum of its parts rather than a separately-maintained number that
    can drift from them.
    """

    input: int = 0
    output: int = 0
    # Reasoning tokens. Billed as output; reported separately by the API.
    thinking: int = 0
    # Prompt tokens served from cache, at a discount. Included in `input`.
    cached: int = 0
    calls: int = 0
    # Which model produced this, for pricing. Blank once two different models
    # have been summed — a mixed total cannot honestly claim one model's price,
    # so `cost_usd` returns None instead of guessing.
    model: str = ""
    # True when at least one summed call ran on a model we have no price for.
    unpriced: bool = False

    @property
    def total(self) -> int:
        """Every token billed. Thinking is part of output, not on top of it."""
        return self.input + self.output

    def cost_usd(self) -> float | None:
        """Advisory USD estimate, or None when it cannot be stated honestly."""
        if self.unpriced or not self.model:
            return None
        price = _price_for(self.model)
        if price is None:
            return None
        per_in, per_out = price
        billable_in = max(0, self.input - self.cached)
        return round(
            (billable_in * per_in
             + self.cached * per_in * _CACHED_DISCOUNT
             + self.output * per_out) / 1_000_000,
            6,
        )

    def __add__(self, other: "Usage") -> "Usage":
        if not isinstance(other, Usage):
            return NotImplemented
        # A total spanning two models can't be priced as either one.
        models = {m for m in (self.model, other.model) if m}
        return Usage(
            input=self.input + other.input,
            output=self.output + other.output,
            thinking=self.thinking + other.thinking,
            cached=self.cached + other.cached,
            calls=self.calls + other.calls,
            model=models.pop() if len(models) == 1 else "",
            unpriced=self.unpriced or other.unpriced or len(models) > 1,
        )

    def as_dict(self) -> dict:
        """JSON-safe form, stored on the job and rendered in the browser.

        `cost_usd` is None rather than 0.0 when unknown — a zero would render as
        "free", which is the one wrong answer.
        """
        return {
            "input": self.input,
            "output": self.output,
            "thinking": self.thinking,
            "cached": self.cached,
            "total": self.total,
            "calls": self.calls,
            "model": self.model,
            "cost_usd": self.cost_usd(),
        }

    @classmethod
    def from_dict(cls, raw) -> "Usage":
        """Read back a stored total. Junk reads as zero, never as an exception —
        a broken usage record must not stop a plan from opening."""
        if not isinstance(raw, dict):
            return cls()
        def _int(key: str) -> int:
            try:
                return max(0, int(raw.get(key) or 0))
            except (TypeError, ValueError):
                return 0
        return cls(
            input=_int("input"),
            output=_int("output"),
            thinking=_int("thinking"),
            cached=_int("cached"),
            calls=_int("calls"),
            model=str(raw.get("model") or ""),
            # A stored total that carried no price keeps that property, or
            # re-summing it would quietly start claiming one.
            unpriced=bool(raw.get("unpriced")) or (raw.get("cost_usd") is None and _int("total") > 0),
        )


def usage_from(response, model_id: str = "") -> Usage:
    """Read `usage_metadata` off a google-genai response.

    Never raises and never guesses: a response without usage metadata (an older
    SDK, a stubbed provider in the tests) reports one call and zero tokens,
    which reads honestly as "we don't know" rather than inventing a number.
    """
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return Usage(calls=1, model=model_id, unpriced=True)

    def _n(*names: str) -> int:
        for name in names:
            value = getattr(meta, name, None)
            if value is None:
                continue
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return 0

    prompt = _n("prompt_token_count")
    thinking = _n("thoughts_token_count", "thinking_token_count")
    candidates = _n("candidates_token_count")
    total = _n("total_token_count")

    # Whether thinking is already inside candidates_token_count has moved
    # between SDK versions. `total_token_count` is the one number the API is
    # sure about, so output is derived from it where possible and only falls
    # back to addition when it is missing.
    output = max(0, total - prompt) if total else candidates + thinking

    return Usage(
        input=prompt,
        output=output,
        thinking=thinking,
        cached=_n("cached_content_token_count"),
        calls=1,
        model=model_id,
        unpriced=_price_for(model_id) is None,
    )


def merge(*usages) -> Usage:
    """Sum any mix of Usage objects and stored dicts into one total."""
    out = Usage()
    for u in usages:
        if u is None:
            continue
        out = out + (u if isinstance(u, Usage) else Usage.from_dict(u))
    return out


def describe(usage: Usage) -> str:
    """One log-friendly line. Used in the server logs and nowhere user-facing."""
    cost = usage.cost_usd()
    money = f", ~${cost:.4f}" if cost is not None else ""
    return (
        f"{usage.total:,} tokens ({usage.input:,} in, {usage.output:,} out"
        + (f", {usage.thinking:,} thinking" if usage.thinking else "")
        + f") over {usage.calls} call(s){money}"
    )
