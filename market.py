"""WHO THIS FILM IS FOR — the country, the language and the money in frame.

⚠ THIS EXISTS BECAUSE AN INDIAN CREATOR'S APP PROMO CAME BACK PRICED IN DOLLARS.
The script said "food delivery app" and never said India, so `world` — which is
read out of the SCRIPT — had no cultural signal to find, and an image model with
no signal draws its default. The default is America: `$4.50` on the phone, an
English app UI, Western faces in the background. Reported over five screenshots,
and the same `$` came back again in the Veo render.

⚠ AND IT IS NOT A `world` FIELD, IT IS A LAYER ON TOP OF ONE. `world` is what
the STORY is (Ancient India, Puranic era, a stone temple); market is who the
FILM IS FOR (India, Hindi, ₹). Usually they agree. For a period piece they must
not be confused — a rupee note does not belong in a Puranic temple — which is
why the money rule below is written as "where prices appear AT ALL", never as
"put prices in".

THE THREE LAYERS, most specific first (see `resolve`):

  1. What the user picked on THIS board's form.
  2. Their account default ("I make films for India").
  3. What the breakdown guessed from the script.

⚠ AND WHEN ALL THREE ARE EMPTY, THE ANSWER IS SILENCE, NOT A GUESS. `no_money`
below asks for no legible price and no currency symbol anywhere. A wrong `$` is
worse than no `$`: one is a mistake the viewer notices, the other is a phone
screen that simply does not show a price, which nobody reads as broken.
"""

from __future__ import annotations

# The fields a market is made of. `units` is derived from the country rather
# than asked for — nobody has ever wanted metric distances in imperial money.
MARKET_FIELDS = ("country", "language", "currency", "units")

# Country → what has to be true on screen. `currency` is written the way it
# should appear in the prompt: the symbol first, because that is the thing the
# model draws, then the name, because a bare "₨" is three different currencies.
#
# ⚠ THIS LIST IS NOT A PERMISSION LIST. An unknown country code or a typed
# country name passes straight through as free text (see `describe`) — the same
# courtesy a custom style or a custom genre gets. It exists so the common
# answers carry the right money and units without the user spelling them out.
COUNTRIES: dict[str, dict[str, str]] = {
    "IN": {"name": "India", "currency": "₹ (Indian rupee)", "units": "metric", "language": "Hindi"},
    "PK": {"name": "Pakistan", "currency": "₨ (Pakistani rupee)", "units": "metric", "language": "Urdu"},
    "BD": {"name": "Bangladesh", "currency": "৳ (Bangladeshi taka)", "units": "metric", "language": "Bengali"},
    "LK": {"name": "Sri Lanka", "currency": "Rs (Sri Lankan rupee)", "units": "metric", "language": "Sinhala"},
    "NP": {"name": "Nepal", "currency": "रू (Nepalese rupee)", "units": "metric", "language": "Nepali"},
    "US": {"name": "United States", "currency": "$ (US dollar)", "units": "imperial", "language": "English"},
    "CA": {"name": "Canada", "currency": "C$ (Canadian dollar)", "units": "metric", "language": "English"},
    "GB": {"name": "United Kingdom", "currency": "£ (pound sterling)", "units": "metric", "language": "English"},
    "AU": {"name": "Australia", "currency": "A$ (Australian dollar)", "units": "metric", "language": "English"},
    "NZ": {"name": "New Zealand", "currency": "NZ$ (New Zealand dollar)", "units": "metric", "language": "English"},
    "AE": {"name": "United Arab Emirates", "currency": "AED (dirham)", "units": "metric", "language": "Arabic"},
    "SA": {"name": "Saudi Arabia", "currency": "SAR (riyal)", "units": "metric", "language": "Arabic"},
    "EG": {"name": "Egypt", "currency": "E£ (Egyptian pound)", "units": "metric", "language": "Arabic"},
    "SG": {"name": "Singapore", "currency": "S$ (Singapore dollar)", "units": "metric", "language": "English"},
    "MY": {"name": "Malaysia", "currency": "RM (Malaysian ringgit)", "units": "metric", "language": "Malay"},
    "ID": {"name": "Indonesia", "currency": "Rp (Indonesian rupiah)", "units": "metric", "language": "Indonesian"},
    "PH": {"name": "Philippines", "currency": "₱ (Philippine peso)", "units": "metric", "language": "Filipino"},
    "TH": {"name": "Thailand", "currency": "฿ (Thai baht)", "units": "metric", "language": "Thai"},
    "VN": {"name": "Vietnam", "currency": "₫ (Vietnamese dong)", "units": "metric", "language": "Vietnamese"},
    "JP": {"name": "Japan", "currency": "¥ (Japanese yen)", "units": "metric", "language": "Japanese"},
    "KR": {"name": "South Korea", "currency": "₩ (South Korean won)", "units": "metric", "language": "Korean"},
    "CN": {"name": "China", "currency": "¥ (Chinese yuan)", "units": "metric", "language": "Chinese"},
    "DE": {"name": "Germany", "currency": "€ (euro)", "units": "metric", "language": "German"},
    "FR": {"name": "France", "currency": "€ (euro)", "units": "metric", "language": "French"},
    "ES": {"name": "Spain", "currency": "€ (euro)", "units": "metric", "language": "Spanish"},
    "IT": {"name": "Italy", "currency": "€ (euro)", "units": "metric", "language": "Italian"},
    "NL": {"name": "Netherlands", "currency": "€ (euro)", "units": "metric", "language": "Dutch"},
    "PL": {"name": "Poland", "currency": "zł (Polish złoty)", "units": "metric", "language": "Polish"},
    "SE": {"name": "Sweden", "currency": "kr (Swedish krona)", "units": "metric", "language": "Swedish"},
    "TR": {"name": "Türkiye", "currency": "₺ (Turkish lira)", "units": "metric", "language": "Turkish"},
    "RU": {"name": "Russia", "currency": "₽ (Russian rouble)", "units": "metric", "language": "Russian"},
    "IL": {"name": "Israel", "currency": "₪ (Israeli shekel)", "units": "metric", "language": "Hebrew"},
    "BR": {"name": "Brazil", "currency": "R$ (Brazilian real)", "units": "metric", "language": "Portuguese"},
    "MX": {"name": "Mexico", "currency": "MX$ (Mexican peso)", "units": "metric", "language": "Spanish"},
    "AR": {"name": "Argentina", "currency": "AR$ (Argentine peso)", "units": "metric", "language": "Spanish"},
    "ZA": {"name": "South Africa", "currency": "R (South African rand)", "units": "metric", "language": "English"},
    "NG": {"name": "Nigeria", "currency": "₦ (Nigerian naira)", "units": "metric", "language": "English"},
    "KE": {"name": "Kenya", "currency": "KSh (Kenyan shilling)", "units": "metric", "language": "Swahili"},
}


def _clean(value) -> str:
    return str(value or "").strip()


def country_entry(country: str) -> dict[str, str]:
    """The catalogue row for a country code or name, or {} if we don't know it."""
    key = _clean(country)
    if not key:
        return {}
    row = COUNTRIES.get(key.upper())
    if row:
        return row
    lowered = key.lower()
    for entry in COUNTRIES.values():
        if entry["name"].lower() == lowered:
            return entry
    return {}


def coerce(raw) -> dict[str, str]:
    """Normalise anything market-shaped to {field: str} over MARKET_FIELDS."""
    if not isinstance(raw, dict):
        return {}
    return {f: _clean(raw.get(f)) for f in MARKET_FIELDS}


def resolve(*layers) -> dict[str, str]:
    """Merge market layers, MOST SPECIFIC FIRST, field by field.

    Field by field and not layer by layer, on purpose: someone whose account
    says India can pick Hindi on one board and English on the next without
    having to restate the country both times.

    ⚠ THE CURRENCY AND UNITS FILL THEMSELVES IN from whichever country wins, and
    only if nothing above already said. Asking a creator to type "₹" after
    picking India is asking them to get it wrong.
    """
    out = {f: "" for f in MARKET_FIELDS}
    for layer in layers:
        for field, value in coerce(layer).items():
            if value and not out[field]:
                out[field] = value

    row = country_entry(out["country"])
    if row:
        # The stored country becomes its readable name, so a prompt never says
        # "Country: IN" — two letters are an identifier, not a place.
        out["country"] = row["name"]
        for field in ("currency", "units"):
            if not out[field]:
                out[field] = row[field]
    return {f: v for f, v in out.items() if v}


def is_empty(market) -> bool:
    """True when nothing at all is known — the case that must draw no money."""
    return not any(coerce(market).values())


# ---------------------------------------------------------------------------
# The prompt text
# ---------------------------------------------------------------------------
_LABELS = {
    "country": "Country / market this film is made for",
    "language": "Language of this audience",
    "currency": "Money in this market is",
    "units": "Measurements are",
}

# ⚠ THE RULE IS WRITTEN AS A CONDITIONAL, AND THAT IS THE WHOLE CRAFT OF IT.
# "Prices are in ₹" invites the model to ADD a price to a shot that had none;
# a mythology board set to India would start growing rupee signs. "WHERE prices
# appear at all" only corrects money that the shot already called for.
_MONEY_RULE = (
    "ON-SCREEN TEXT AND MONEY — anything readable inside the frame (a phone or "
    "computer screen, an app interface, a shop sign, a price tag, packaging, a "
    "menu, a receipt) belongs to this audience. WHERE text appears at all it is "
    "written in {language_txt}; WHERE a price or a currency appears at all it is "
    "shown in {currency}, formatted the way {country_txt} writes money. Never "
    "use the dollar sign or any other market's currency. If you cannot render "
    "the text correctly, show the surface WITHOUT legible text — a clean "
    "interface with no readable words or numbers is right, invented foreign "
    "text is not."
)

# ⚠ AND THIS IS THE ONE THAT RUNS WHEN NOBODY SAID. Silence beats a guess: an
# app screen with no price reads as a design choice, a `$` on an Indian film
# reads as a mistake, and only one of those gets reported.
_NO_MONEY_RULE = (
    "ON-SCREEN TEXT AND MONEY — no audience or market has been set for this "
    "film, so do NOT invent one. Any screen, sign, price tag, menu or "
    "packaging in frame must carry NO legible price, NO currency symbol of any "
    "kind (no $, no €, no £) and no country-specific branding or signage. Show "
    "these surfaces clean: an interface with shapes and images but no readable "
    "words or numbers. Guessing a market wrongly is worse than showing none."
)


def describe(market) -> str:
    """The market as prompt lines, or "" when nothing is known."""
    data = coerce(market)
    lines = [
        f"{label}: {data[field]}."
        for field, label in _LABELS.items()
        if data.get(field)
    ]
    return " ".join(lines)


def on_screen_text_rule(market) -> str:
    """What may be written and priced inside the frame. Never empty.

    ⚠ RETURNS THE NO-MONEY RULE WHEN THE MARKET IS UNKNOWN, which is why every
    caller can append it unconditionally. A caller that skipped it for an unset
    market would be back to the model's American default, which is the bug.
    """
    data = coerce(market)
    if not data.get("country") and not data.get("language") and not data.get("currency"):
        return _NO_MONEY_RULE
    return _MONEY_RULE.format(
        language_txt=data.get("language") or "the language this audience reads",
        currency=data.get("currency") or "this market's own currency, never dollars",
        country_txt=data.get("country") or "this market",
    )


def context(market) -> str:
    """The whole market block for an image prompt: who it is for, then the rule."""
    described = describe(market)
    rule = on_screen_text_rule(market)
    if not described:
        return rule
    return f"THIS FILM'S AUDIENCE — {described} {rule}"


# Currency signs worth naming in a Veo negative prompt. Veo takes a list of
# things to keep OUT, and the dollar sign is the one that actually shows up.
_ALL_SIGNS = ("$", "€", "£", "¥", "₹", "₩", "₽", "₺", "₦", "₱", "฿", "₫", "₪")


def negative_terms(market) -> str:
    """Currency signs this film must NOT show, as a Veo negative prompt clause.

    Everything except this market's own. With no market set, all of them —
    which is the same "show no money" answer the image side gives.
    """
    data = coerce(market)
    own = data.get("currency") or ""
    unwanted = [s for s in _ALL_SIGNS if s not in own]
    return (
        "on-screen prices in a foreign currency, "
        + ", ".join(f"{s} symbol" for s in unwanted)
        + ", misspelt or invented on-screen text"
    )
