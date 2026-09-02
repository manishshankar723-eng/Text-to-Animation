"""ONE IMAGE FROM ONE SENTENCE — the Media pane's ✨.

    "i want add ai icon in + upload assets box panel in media panel so user click
     Ai icon so user get this popup … so generate any type of image with text
     prompt fill and user click generate images buttun and gemini generate image
     an come back in media in image tab name under and in layer image layer come
     generated image"

---------------------------------------------------------------------------
WHAT THIS FILE IS ACTUALLY GUARDING
---------------------------------------------------------------------------
⚠ THIS IS NOT THE SHOT GENERATOR WITH THE BOARD LEFT OUT, and the checks below
are mostly about keeping the two apart. `POST …/frames/{id}/neighbour` draws a
SHOT: the board's style variant, its references, its written bible, the shots
either side of the gap. This one draws whatever the sentence says — a title
card, a texture, an inset — so it must send NONE of that, or "a rain-soaked neon
alley" comes back as a pencil thumbnail of one, in whatever style the board
happens to be in.

⚠ AND IT ANSWERS WITH THE SAME `AnimaticMediaItem` A FILE UPLOAD DOES. That is
what lets the client place it exactly as it places a dropped picture — into the
library and onto the overlay Images lane — without one code path downstream
learning that it was generated.

⚠ THE MODEL IS NEVER CALLED. `gemini_client.generate_image` is replaced with a
stub that records what it was handed; the point is the routing, the cropping and
what reaches the model, none of which needs a paid call.

    python tests/image_generate_check.py
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ **EVERY STORE PINNED INTO A THROWAWAY DIRECTORY, BEFORE ANY `server.*`
# IMPORT.** `server/config.py` reads the environment once, at import time, so
# without this line the suite boots against the developer's real `.env` — it
# registers its test accounts in the production database and spends real monthly
# quota, and then fails when billing refuses it. G13; see `tests/_sandbox.py`.
from _sandbox import pin  # noqa: E402

_TMP = pin("image_generate_check_")

from fastapi.testclient import TestClient
from PIL import Image

import gemini_client
from server.jobs import get_store
from server.main import app

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


client = TestClient(app)
store = get_store()


def register():
    email = f"_imggen_{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "imggen-pass-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, email


auth, email = register()
other_auth, _ = register()

print(f"\nstore: {type(store).__name__}\n")

r = client.post(
    "/animatics",
    headers=auth,
    json={"title": "Image gen", "settings": {"aspect_ratio": "9:16"}},
)
assert r.status_code == 201, r.text
job_id = r.json()["job_id"]

# ---------------------------------------------------------------------------
print("[1] the dialog can name the model before anything is spent")
r = client.get("/animatics/image-model", headers=auth)
check("-> 200", r.status_code, 200)
check("a model is named", bool(r.json()["model"]))
check("and the backend it runs on", bool(r.json()["provider"]))
# ⚠ DECLARED BEFORE `/{job_id}`, or "image-model" is read as a project id.
check("it is not shadowed by the project route", r.json().get("model") is not None)
check("and it is authed", client.get("/animatics/image-model").status_code, 401)

# ---------------------------------------------------------------------------
print("\n[2] the drawing comes back as an ORDINARY UPLOAD")
sent = {}


def fake_image(description, **kwargs):
    sent["description"] = description
    sent.update(kwargs)
    # Deliberately the WRONG shape: 4:3 when 9:16 was asked for, so the crop
    # below has something to do.
    return Image.new("RGB", (400, 300), (90, 20, 40))


gemini_client.generate_image = fake_image

r = client.post(
    f"/animatics/{job_id}/images/generate",
    headers=auth,
    json={"prompt": "  A rain-soaked neon alley at night, deep blues and hot pink  "},
)
check("-> 200", r.status_code, 200)
body = r.json()
item = body["item"]
check("there is a file behind it", bool(item["upload_id"]))
check("it reports its real size", item["width"] > 0 and item["height"] > 0)
check("the model that drew it is named", bool(body["model"]))

from server.animatics import _image_path  # noqa: E402  (after the app is built)

path = _image_path(job_id, item["upload_id"])
check("the picture is in the animatic's own media folder", os.path.isfile(path))

# ⚠ THE SAME FOLDER AND ID SPACE AS AN UPLOAD, so the ordinary media route
# serves it with no special case anywhere.
r = client.get(f"/animatics/{job_id}/media/{item['upload_id']}", headers=auth)
check("…and the ordinary media route serves it", r.status_code, 200)
check("…as an image", r.headers["content-type"].startswith("image/"))

# ---------------------------------------------------------------------------
print("\n[3] the shape asked for is the shape delivered")
# The stub returned 4:3 and the project is 9:16, so the crop has to have run.
check("the project's own shape is the default", sent.get("aspect_ratio"), "9:16")
with Image.open(path) as im:
    w, h = im.size
check("the picture is cropped to it, not left as the model framed it",
      abs((w / h) - (9 / 16)) < 0.02, True)
check("…and it was ASKED for as well as cropped to", sent.get("aspect_ratio"), "9:16")

r = client.post(
    f"/animatics/{job_id}/images/generate",
    headers=auth,
    json={"prompt": "a square logo plate", "aspect_ratio": "1:1"},
)
check("-> 200", r.status_code, 200)
check("an explicit shape overrides the project's", sent.get("aspect_ratio"), "1:1")
with Image.open(_image_path(job_id, r.json()["item"]["upload_id"])) as im:
    check("…and it is delivered square", im.width, im.height)

# ---------------------------------------------------------------------------
print("\n[4] ⚠ NOTHING ABOUT THE STORYBOARD IS SENT")
# The whole difference between this and the shot generator. A style, a bible, a
# reference or a neighbour reaching the model here is the bug this guards.
for leak in ("style", "cast", "assets", "world", "story_context", "characters",
             "reference_images", "anchor_index", "board_panels"):
    check(f"no `{leak}` reaches the model", leak in sent, False)
check("the user's sentence is the whole brief, trimmed and otherwise untouched",
      sent.get("description"), "a square logo plate")

# ---------------------------------------------------------------------------
print("\n[5] the card is named after the words that made it")
r = client.post(
    f"/animatics/{job_id}/images/generate",
    headers=auth,
    json={"prompt": "A hand-painted title card, cracked gold lettering on a deep navy ground"},
)
name = r.json()["name"]
check("-> 200", r.status_code, 200)
check("it opens with the prompt", name.startswith("A hand-painted title card"))
check("it is cut short", len(name) <= 44, True)
check("…at a word, not mid-word", "…" in name and not name.endswith(" …"))

from server.animatics import _image_name_from_prompt  # noqa: E402

check("a short prompt is left whole",
      _image_name_from_prompt("A red door"), "A red door")
check("whitespace is collapsed",
      _image_name_from_prompt("  A   red\n door  "), "A red door")
check("a prompt of nothing still names the card",
      _image_name_from_prompt("   "), "Generated image")

# ---------------------------------------------------------------------------
print("\n[6] it is RETURNED, not placed — the client owns the timeline")
saved = client.get(f"/animatics/{job_id}", headers=auth).json()
check("no overlay was written for us", saved["overlays"], [])
check("no clip was written for us", saved["frames"], [])
check("and no library card either", saved.get("assets") in (None, []), True)

# ---------------------------------------------------------------------------
print("\n[7] the refusals")
r = client.post(f"/animatics/{job_id}/images/generate", headers=auth, json={"prompt": "   "})
check("a blank prompt -> 422 or 400", r.status_code in (400, 422), True)


def blocked(*_a, **_k):
    return None


gemini_client.generate_image = blocked
r = client.post(f"/animatics/{job_id}/images/generate", headers=auth, json={"prompt": "x"})
check("a model that returns nothing -> 502", r.status_code, 502)
check("…and says a filter is the likely reason", "safety filter" in r.json()["detail"])


def explodes(*_a, **_k):
    raise RuntimeError("the backend fell over")


gemini_client.generate_image = explodes
r = client.post(f"/animatics/{job_id}/images/generate", headers=auth, json={"prompt": "x"})
check("a backend that fails -> 502", r.status_code, 502)
check("…with the real reason in it", "fell over" in r.json()["detail"])

r = client.post(
    f"/animatics/{job_id}/images/generate", headers=other_auth, json={"prompt": "x"}
)
check("someone else's project -> 404", r.status_code, 404)

client.delete(f"/animatics/{job_id}", headers=auth)

# ---------------------------------------------------------------------------
print("\n[8] the ＋ card is still PINNED to the top of the Media pane")
# ⚠ THE ONE REGRESSION THIS FEATURE ACTUALLY CAUSED, and no test could see it.
# The ✨ had to go in a wrapper (the card is a `<button>` and a button inside a
# button does not render), and FOUR rules in animatic-editor.css select the card
# as a DIRECT CHILD of `.an-media-body`: the two `:has()` that decide the pane's
# top padding and `--an-drop-h`, the sticky block itself, and the heading offset
# that reads that variable. Wrapping it made the card a grandchild, so all four
# stopped matching and the Media tab silently took the Shapes tab's layout — the
# card scrolled away and the headings pinned to the very top. Reported as "i
# scrol bar move so my +add … box panle go up but i want still not move".
#
# A source read, because the fault is a SELECTOR not matching a DOM shape, and
# the two live in different files — which is exactly the pair that drifts.
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
editor = (ROOT / "client/src/components/AnimaticEditor.jsx").read_text(encoding="utf-8")
css = (ROOT / "client/src/styles/animatic-editor.css").read_text(encoding="utf-8")

check("the ✨ is a SIBLING of the card, in a wrapper",
      'className="an-asset-add"' in editor)
check("…and the card is still inside it", 'className="an-asset-drop"' in editor)
for rule in [
    ".an-media-body:not(:has(> .an-asset-add))",   # --an-drop-h on the Shapes tab
    ".an-media-body:has(> .an-asset-add)",          # no top padding
    ".an-media-body > .an-asset-add {",             # the sticky block itself
]:
    check(f"`{rule}` selects the WRAPPER, not the card", rule in css)
check("⚠ NOTHING STILL SELECTS THE CARD AS A DIRECT CHILD OF THE PANE",
      ".an-media-body > .an-asset-drop" in css, False)
check("the pinned box is still a fixed height the headings can share",
      "height: var(--an-drop-h);" in css)
check("…and the headings still pin directly under it",
      "top: var(--an-drop-h);" in css)

print()
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("A sentence becomes a picture, and the picture is an ordinary upload.")
