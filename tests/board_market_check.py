"""AN INDIAN CREATOR'S FILM IS NOT PRICED IN DOLLARS — and an unset one shows
no price at all.

From the same report as `board_look_check.py`, five screenshots of a 28-panel
mobile-app promo:

    "sabse pahle ye video indian user ne bana tha magar usko video mai $ dikhne
     laga ai ne generate kiya … is duniya mai bahut religion, language hai kaise
     gemini samjhega ki kis desh ka hai to uske hisab se hi sab dikhna chahiye"

---------------------------------------------------------------------------
1. `world` COULD NOT FIX THIS, AND IT WAS ALREADY THERE
---------------------------------------------------------------------------
`world` is read out of the SCRIPT. The script said "food delivery app" and never
said India, so there was no cultural signal to find — and an image model handed
no signal draws its default, which is America. `$4.50` on the phone, an English
app UI, and the same `$` again when the shot was re-rendered with Veo.

⚠ THE MISSING FACT IS NOT IN THE SCRIPT AT ALL. It is who the film is FOR, which
only the creator knows. So it is asked for, in two places — an account default
and this board's form — and GUESSED only as a last resort.

---------------------------------------------------------------------------
2. THE THREE LAYERS, AND WHY THE FOURTH ANSWER IS SILENCE
---------------------------------------------------------------------------
    1. what this board's form said     (most specific)
    2. the account default
    3. what the breakdown read off the script

⚠ AND WHEN ALL THREE ARE EMPTY, NOTHING IS GUESSED. `market.on_screen_text_rule`
then asks for no legible price and no currency symbol anywhere. An app screen
with no price reads as a design choice; a `$` on an Indian film reads as a
mistake — and only one of those gets reported. This test pins that asymmetry,
because "fill in a sensible default" is exactly the instinct that caused the bug.

---------------------------------------------------------------------------
3. AND THE MONEY RULE IS A CONDITIONAL, NOT AN INSTRUCTION
---------------------------------------------------------------------------
"Prices are in ₹" would invite the model to ADD a price to a shot that never had
one — a mythology board set to India growing rupee signs inside a Puranic
temple. The rule says "WHERE a price appears at all", which only corrects money
the shot already called for. That is why market is kept distinct from `world`
even though the two ride in one dict.

Run:
    python tests/board_market_check.py
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

failures: list[str] = []


def check(label: str, ok: bool) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}")
    if not ok:
        failures.append(label)


def read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


import market  # noqa: E402 — after sys.path is set

# ---------------------------------------------------------------------------
print("\n[1] the three layers resolve most-specific-first")

form = {"country": "IN", "language": "Hinglish"}
account = {"country": "US", "language": "English"}
guess = {"country": "GB"}

check("the form beats the account default",
      market.resolve(form, account, guess)["country"] == "India")
check("the account default beats the script's guess",
      market.resolve({}, account, guess)["country"] == "United States")
check("the script's guess is used when nothing else answered",
      market.resolve({}, {}, guess)["country"] == "United Kingdom")
check("⚠ layers merge FIELD BY FIELD, not whole-layer — an account country "
      "survives a form that only chose a language",
      market.resolve({"language": "Tamil"}, {"country": "IN"}, {}) ==
      {"country": "India", "language": "Tamil",
       "currency": "₹ (Indian rupee)", "units": "metric"})

# ---------------------------------------------------------------------------
print("\n[2] the money is looked up, never typed")

india = market.resolve({"country": "IN"}, {}, {})
check("picking India fills in the rupee",
      india["currency"] == "₹ (Indian rupee)")
check("…and the units that go with it",
      india["units"] == "metric")
check("a country CODE resolves to its readable name — a prompt must never "
      "say 'Country: IN'",
      india["country"] == "India")
check("a country NAME resolves too, for a saved board or a typed answer",
      market.resolve({"country": "India"}, {}, {})["currency"] == "₹ (Indian rupee)")
check("an unknown country is passed through, not rejected",
      market.resolve({"country": "Atlantis"}, {}, {})["country"] == "Atlantis")
check("the US is the one market that is NOT metric",
      market.resolve({"country": "US"}, {}, {})["units"] == "imperial")

# ---------------------------------------------------------------------------
print("\n[3] ⚠ nothing known means NO money, not a guessed one")

nothing = market.resolve({}, {}, {})
check("all three layers empty resolves to nothing at all",
      nothing == {} and market.is_empty(nothing))
silent = market.on_screen_text_rule(nothing)
check("⚠ …and that produces the NO-MONEY rule, not an empty string",
      "do NOT invent one" in silent and "NO currency symbol" in silent)
check("…which names the signs it is refusing, dollar first",
      "no $" in silent and "no €" in silent and "no £" in silent)
check("…and says what to draw instead, so the model is not just forbidden",
      "no readable words or numbers" in silent)
check("⚠ the rule is NEVER empty — a caller can append it unconditionally, "
      "which is what stops an unset market falling back to the US default",
      bool(market.on_screen_text_rule({})) and bool(market.on_screen_text_rule(india)))

# ---------------------------------------------------------------------------
print("\n[4] the money rule is conditional, so it corrects but never adds")

rule = market.on_screen_text_rule(india)
check("⚠ it says WHERE a price appears, not 'add prices' — a mythology board "
      "set to India must not grow rupee signs in a Puranic temple",
      "WHERE a price or a currency appears at all" in rule)
check("…same for text: WHERE text appears at all",
      "WHERE text appears at all" in rule)
check("the dollar is refused by name",
      "Never use the dollar sign" in rule)
check("⚠ and the escape hatch is 'show it blank', not 'do your best' — "
      "invented foreign text is worse than none",
      "WITHOUT legible text" in rule and "invented foreign text is not" in rule)
check("the surfaces are named, so 'text' is not left abstract",
      all(w in rule for w in ("phone", "app interface", "price tag", "menu")))

# ---------------------------------------------------------------------------
print("\n[5] every image prompt carries it")

g = read("gemini_client.py")
check("the panel prompt appends the market unconditionally",
      "parts.append(build_market_context(world))" in g)
check("⚠ …and so does the PROP / BACKGROUND reference, which is the one that "
      "gets baked into every panel the object appears in",
      "prompt = f\"{prompt} {build_market_context(world)}\"" in g)
check("⚠ the character T-pose sheet deliberately does NOT — one figure on "
      "white with no text in it has no price tag to get wrong",
      "build_market_context" not in g.split("def generate_character_reference")[1]
      .split("def ")[0])
check("market is kept apart from the story's world in the prompt builders",
      "def build_world_context" in g and "def build_market_context" in g)

# ---------------------------------------------------------------------------
print("\n[6] it is resolved on the server, at every drawing route")

main = read("server", "main.py")
check("there is ONE resolver, not three",
      main.count("def _resolve_market(") == 1)
check("⚠ …and all three drawing routes use it — cast sheets and prop refs are "
      "drawn BEFORE the board job exists, so a board-only fix would leave them "
      "priced in dollars",
      main.count("_resolve_market(") == 4)  # 1 def + 3 call sites
check("the account default is read from the user record",
      'account.get("default_country")' in main)
check("the form's pick and the script's guess arrive distinguishable",
      "chosen.model_dump() if chosen else {}" in main
      and 'out.get("country")' in main)

schemas = read("server", "schemas.py")
_market_model = schemas.split("class Market(BaseModel)")[1].split("\nclass ")[0]
check("⚠ Market asks ONLY what a person can answer — no currency field, "
      "because making someone type '₹' after picking India invites a typo "
      "into every price in the film",
      "class Market(BaseModel)" in schemas
      and "country: str = Field(" in _market_model
      and "language: str = Field(" in _market_model
      and "currency: str = Field(" not in _market_model
      and "units: str = Field(" not in _market_model)
check("…and World carries the resolved market downstream",
      all(f"    {f}: str = \"\"" in schemas.split("class World")[1].split("class ")[0]
          for f in ("country", "language", "currency", "units")))

breakdown = read("script_breakdown.py")
check("⚠ the breakdown is told to LEAVE THE MARKET BLANK unless the script "
      "actually says — its guess is the weakest layer, and a wrong guess is "
      "the whole bug",
      "LEAVE IT EMPTY otherwise" in breakdown
      and "GUESSED_MARKET_FIELDS" in breakdown)

# ---------------------------------------------------------------------------
print("\n[7] the video half is the same film")

animatics = read("server", "animatics.py")
check("a project made from a board inherits that board's audience",
      '"market": _board_market(board) if source_id else {},' in animatics)
check("⚠ …copied, not looked up — editing the board later must not relight a "
      "video already being cut",
      "Copied rather than looked up" in animatics)
check("there is ONE Veo localiser",
      animatics.count("def _localise_veo(") == 1)
check("the animate button uses it",
      "_localise_veo(\n        record.prompt" in animatics
      or "veo_prompt, veo_negative = _localise_veo(" in animatics)
check("⚠ …and so does the long-video render, or one film would come out in "
      "two currencies split down the middle",
      "_localise_veo(shot.prompt, render.negative_prompt, job)" in read("server", "videos.py"))
check("other markets' currency signs go on the NEGATIVE prompt too",
      "negative_terms" in animatics and "$ symbol" in market.negative_terms({}))
check("⚠ the instruction stays English while the on-screen text localises — "
      "Veo follows English camera direction measurably better",
      "instructions English" in read("director.py")
      or "THE RULE IS APPENDED IN ENGLISH" in animatics)

director_router = read("server", "director.py")
check("the Director's captions fall back to the audience's language",
      '(job.params or {}).get("market") or {}).get("language")' in director_router)
check("…without overwriting an explicit 'no language' choice",
      "it is NOT written back onto the project" in director_router)

# ---------------------------------------------------------------------------
print("\n[8] the user can actually say it — in both places")

options = read("client", "src", "storyboardOptions.js")
check("the country list exists and leads with the honest empty answer",
      "MARKET_COUNTRIES" in options
      and 'id: "", label: "Not set — show no prices"' in options)
check("⚠ Hinglish is offered, because that is what Indian creators caption in",
      '{ id: "Hinglish"' in options)
check("the list warns that it must track market.py",
      "KEEP `MARKET_COUNTRIES` IN STEP WITH `COUNTRIES` IN market.py" in options)

form_ui = read("client", "src", "components", "ScriptToStoryboard.jsx")
check("the storyboard form asks",
      "Audience" in form_ui and "MARKET_COUNTRIES.map" in form_ui)
check("⚠ …and says out loud what an unset market means, so a blank app screen "
      "in a finished panel does not read as a bug",
      "will show no prices or readable text" in form_ui)
check("the account default prefills it",
      "p?.default_country" in form_ui)
check("⚠ changing the audience invalidates the board — switching India to the "
      "US changes every price tag in it",
      "market: effectiveMarket()," in form_ui)
check("it reaches the cast step, the props step and the create call",
      form_ui.count("market={effectiveMarket()}") == 2
      and "market: effectiveMarket()," in form_ui)

profile = read("client", "src", "components", "Profile.jsx")
check("the profile carries the account default",
      "default_country" in profile and "default_language" in profile)
check("…and it saves with the rest of the storyboard defaults",
      '"default_country",' in profile.split("SaveRow")[-1] or
      '"default_country",' in profile)

api = read("client", "src", "api.js")
check("the client sends the market on all three calls",
      api.count("body.market = market") == 2 and "market: market || null," in api)

auth = read("server", "auth.py")
users = read("server", "users.py")
check("the account fields are readable, writable and whitelisted",
      "default_country: str = \"\"" in auth
      and "default_country: str | None" in auth
      and '"default_country",' in users)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("The money on screen belongs to the audience — and when nobody said who "
      "that is, there is no money on screen.")
