"""A SHOT HOLDS ITS OWN LINE — and the dialogue is editable before it is read.

Two reports, one dialog, with four screenshots:

    "when i generate voiceover of my Story..image layer in timline Voicerover
     buttun so Geneate perfectly voiceover and caption and placement Starting is
     good ony but caption and voicerover goes overlap other image shots … so my
     shot 9 image cover voiceover lenght and set like image 4 and other voicer
     and capyion arrange like this"

    "i want i see my Storyborad Dialouge in here (read the dialogue aloude) in
     pop-up so user see what dialouge generte so user look if user want chnage so
     user change/edit Dialouge … and if posible so add character name like so
     user understand what charater voicerover and with gender men/women,
     boy/girl, child and grand father"

---------------------------------------------------------------------------
1. THE OVERLAP, AND WHY IT WAS BUILT IN
---------------------------------------------------------------------------
A line is laid at the start of the shot it belongs to. The shot holds for two
seconds; the line takes ten. Nothing moved the picture, so the line — and the
caption built from it — ran straight over the four shots after it:

    image   [S9][S10][S11][S12][S13]        <- before
    audio   |========= S9's line =========|

    image   [ S9 ..................... ][S10][S11][S12][S13]   <- after
    audio   |========= S9's line =========|

The room comes from the row itself, exactly as it does for a Veo take
(`spreadPanelsForRenders`): the shot that owns the line is STRETCHED to cover
it, and the shots after it are pushed clear.

⚠ WHAT THIS FILE IS ACTUALLY GUARDING: ONE CLOCK. There used to be two — `tts`
advanced its own by `line + gap` while the pictures never moved at all — and two
clocks agree right up until a shot holds LONGER than its line, at which point the
audio runs ahead of the pictures and every line after it is early. So the checks
below are mostly about the boring shots: the silent one in the middle, the one
whose line fits, the second run that must move nothing. If those drift, the bug
comes back somewhere in the middle of a forty-shot board where nobody looks.

---------------------------------------------------------------------------
2. THE SHEET
---------------------------------------------------------------------------
`GET /animatics/{id}/dialogue` is free, calls no model, and answers with the
lines, the shot each belongs to, a PERSONA guessed from the board's cast sheet,
and the two pickers. The persona is the only thing that carries an age and a sex
to the model — it writes the stage direction the line is read with — and the
direction must never reach the CAPTIONS, which is checked here by looking at
what the stubbed model was handed versus what was written on the timeline.

    python tests/voiceover_fit_check.py

No backend, no browser, no ffmpeg, and NO MODEL CALL: `tts.speak` is stubbed
with silence of a known length, which is exactly what makes the arithmetic
below checkable.
"""

import os
import sys
import uuid
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ **EVERY STORE PINNED INTO A THROWAWAY DIRECTORY, BEFORE ANY `server.*`
# IMPORT.** `server/config.py` reads the environment once, at import time, so
# without this line the suite boots against the developer's real `.env` — it
# registers its test accounts in the production database and spends real monthly
# quota, and then fails when billing refuses it. G13; see `tests/_sandbox.py`.
from _sandbox import pin  # noqa: E402

_TMP = pin("voiceover_fit_check_")

from fastapi.testclient import TestClient

import captions
import tts
from server import config
from server.animatics import run_voiceover
from server.jobs import get_store
from server.main import app
from server.schemas import JobKind, JobStatus

failures: list[str] = []


def check(label, got, want=True):
    good = (got == want)
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  (got {got!r})"))
    if not good:
        failures.append(label)


client = TestClient(app)
store = get_store()


def register():
    email = f"_vo_{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "vo-pass-12345"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, email


auth, email = register()
# \u26a0 ON AN UNLIMITED TIER, AND NOT BECAUSE THIS SUITE CARES ABOUT TIERS. It
# creates three animatics while testing how a voiceover is TIMED, and Phase 5's
# `require_quota("projects")` refuses the third on the free tier - so without
# this line the run dies with a 402 about billing in a file about audio, and
# sends the next person hunting in the wrong module. Same fix, same reason, as
# the note at the top of `features_check.py`.
from server import users as _users  # noqa: E402

_users.set_tier(email, "production")
print(f"\nstore: {type(store).__name__}\n")

# ---------------------------------------------------------------------------
# The model, stubbed. Every line carries a marker and the stub returns silence
# of that marker's length, so the layout below is arithmetic and not a guess.
# ---------------------------------------------------------------------------
SPEECH_MS = {"[long]": 9000, "[short]": 1000, "[edited]": 500}
PROMPTS: list[tuple[str, str]] = []  # (voice, prompt) in the order they were sent


def stub_speak(text, *, voice=None, provider=None):
    PROMPTS.append((voice, text))
    for mark, ms in SPEECH_MS.items():
        if mark in text:
            return tts.silence(ms)
    return tts.silence(500)


tts.speak = stub_speak  # noqa: E305 — the point of the stub

# ---------------------------------------------------------------------------
# A board: two spoken shots, two silent ones, and a cast sheet to guess from.
# Written straight into the store, like every other board test here — the
# breakdown that normally writes this shape is a paid call.
# ---------------------------------------------------------------------------
PANELS = [
    {"index": 0, "description": "The old man on the step.",
     "dialogue": [{"character": "DADAJI", "line": "Sit with me a while. [long]"}]},
    {"index": 1, "description": "The empty lane.", "dialogue": []},
    {"index": 2, "description": "Priya runs in.",
     "dialogue": [{"character": "PRIYA", "line": "I found it! [short]"}]},
    {"index": 3, "description": "The lamp goes out.", "dialogue": []},
]
CAST = [
    {"name": "DADAJI", "description": "an elderly man, age 72, white-haired"},
    {"name": "PRIYA", "description": "a girl, 9 years old, in a school uniform"},
]
board = store.create(
    character_name="The Step", kind=JobKind.STORYBOARD, owner=email, params={"shots": []}
)
store.update(
    board.job_id,
    status=JobStatus.SUCCEEDED,
    result={"panels": PANELS, "characters": CAST},
)

# ---------------------------------------------------------------------------
# The timeline. Four shots laid end to end at 2s each, and ONE VEO TAKE of shot
# 3 sitting on the track above it — the take has to travel with its panel.
#
#   track 1                            [ take of Shot 3 ....... ]
#   track 0   [S1][S2][S3][S4]
#              0   2   4   6  8s
# ---------------------------------------------------------------------------
FRAMES = [
    {"id": "fr0", "src": {"kind": "panel", "storyboard_id": board.job_id, "index": 0},
     "duration_ms": 2000, "start_ms": 0, "track": 0, "label": "Shot 1"},
    {"id": "fr1", "src": {"kind": "panel", "storyboard_id": board.job_id, "index": 1},
     "duration_ms": 2000, "start_ms": 2000, "track": 0, "label": "Shot 2"},
    {"id": "fr2", "src": {"kind": "panel", "storyboard_id": board.job_id, "index": 2},
     "duration_ms": 2000, "start_ms": 4000, "track": 0, "label": "Shot 3"},
    {"id": "fr3", "src": {"kind": "panel", "storyboard_id": board.job_id, "index": 3},
     "duration_ms": 2000, "start_ms": 6000, "track": 0, "label": "Shot 4"},
    {"id": "vr0", "kind": "video",
     "src": {"kind": "video", "storyboard_id": board.job_id, "index": 2,
             "upload_id": uuid.uuid4().hex[:12]},
     "duration_ms": 5000, "start_ms": 4000, "track": 1, "label": "Shot 3"},
]


def new_animatic(title="Voiceover fit"):
    r = client.post("/animatics", headers=auth, json={"title": title, "frames": FRAMES})
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def project(job_id):
    r = client.get(f"/animatics/{job_id}", headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def spans(job_id):
    """{frame id: (start, end)} as the project now stands."""
    out = {}
    for f in project(job_id)["frames"]:
        start = int(f["start_ms"] or 0)
        out[f["id"]] = (start, start + int(f["duration_ms"]))
    return out


GAP = tts.GAP_MS

# ---------------------------------------------------------------------------
print("[1] the dialogue sheet — free, and it says who is speaking")
job_id = new_animatic()
r = client.get(f"/animatics/{job_id}/dialogue", headers=auth)
check("-> 200", r.status_code, 200)
sheet = r.json()
lines = sheet["lines"]
check("both spoken shots are listed, the two silent ones are not", len(lines), 2)
check("in the order they will be read",
      [l["start_ms"] for l in lines], [0, 4000])
check("each line names its shot, so the sheet reads as a script",
      [l["shot"] for l in lines], ["Shot 1", "Shot 3"])
check("and names its speaker", [l["character"] for l in lines], ["DADAJI", "PRIYA"])
check("AN ELDERLY MAN IS CAST AS ONE, from the cast sheet's own words",
      lines[0]["persona"], "grandfather")
check("...and a nine-year-old girl as a girl", lines[1]["persona"], "girl")
check("the line's own voice is left empty, so the persona casts it",
      [l["voice"] for l in lines], ["", ""])
check("the sheet carries the shot's current hold, for the dialog to show",
      lines[0]["hold_ms"], 2000)
check("the voice list comes from the SERVER, not from the JSX",
      len(sheet["voices"]) == len(tts.CAST) and sheet["voices"][0]["tone"] != "")
check("every persona in the picker is one the model call knows",
      sorted(p["key"] for p in sheet["personas"]) == sorted(tts.PERSONAS))
check("...and each one names the voice it casts",
      all(p["voice"] in tts.VOICES for p in sheet["personas"]))
check("the dialog can tell 'no dialogue' from 'not a board'", sheet["from_board"], True)

# ---------------------------------------------------------------------------
print("\n[2] the price is the price of the words on screen")
r = client.post(f"/animatics/{job_id}/voiceover/estimate", headers=auth, json={})
check("-> 200", r.status_code, 200)
from_board = r.json()
check("two lines quoted", from_board["lines"], 2)
r = client.post(
    f"/animatics/{job_id}/voiceover/estimate",
    headers=auth,
    json={"lines": [{"frame_id": "fr0", "text": "Hi.", "persona": ""}]},
)
edited = r.json()
check("an edited sheet is quoted instead of the board", edited["lines"], 1)
check("...and it is cheaper than the board's own two lines",
      edited["characters"] < from_board["characters"])
check("A PERSONA IS PART OF THE PRICE — the direction is sent too",
      client.post(
          f"/animatics/{job_id}/voiceover/estimate", headers=auth,
          json={"lines": [{"frame_id": "fr0", "text": "Hi.", "persona": "grandfather"}]},
      ).json()["characters"] > edited["characters"])
check("a line pointing at a clip that isn't on this timeline is dropped",
      client.post(
          f"/animatics/{job_id}/voiceover/estimate", headers=auth,
          json={"lines": [{"frame_id": "gone", "text": "Hi."}]},
      ).json()["lines"], 0)
# ⚠ QUOTED AND READ MUST BE THE SAME SET. The layout only walks the board's own
# picture row, so a line hung on a Veo take has nothing to stretch and would
# never be spoken — and a price for silence is the worst kind of wrong price.
check("...and so is one hung on a clip the layout would skip",
      client.post(
          f"/animatics/{job_id}/voiceover/estimate", headers=auth,
          json={"lines": [{"frame_id": "vr0", "text": "Hi."}]},
      ).json()["lines"], 0)

# ---------------------------------------------------------------------------
print("\n[3] the run: each shot stretches to hold its own line")
PROMPTS.clear()
run_voiceover(job_id, {"voice": "Kore", "add_captions": True, "replace": True})
after = spans(job_id)

# Shot 1's line is 9s of speech into a 2s hold, so the shot becomes 9s + the
# breath after it, and everything downstream moves by exactly that.
check("THE SPOKEN SHOT COVERS ITS LINE", after["fr0"], (0, 9000 + GAP))
check("the silent shot after it is pushed clear, not stretched",
      after["fr1"], (9000 + GAP, 11000 + GAP))
check("A SHOT WHOSE LINE FITS IS MOVED BUT NOT STRETCHED",
      after["fr2"], (11000 + GAP, 13000 + GAP))
check("THE VEO TAKE TRAVELS WITH ITS PANEL, by the panel's delta",
      after["vr0"], (11000 + GAP, 16000 + GAP))
check("...and the shot after that clears the TAKE, not just the panel",
      after["fr3"], (16000 + GAP, 18000 + GAP))
check("no picture ever moved backwards",
      all(after[k][0] >= s for k, s in
          {"fr0": 0, "fr1": 2000, "fr2": 4000, "fr3": 6000, "vr0": 4000}.items()))

print("\n[4] ...and the sound is where the picture is")
doc = project(job_id)
# ⚠ ONE CLIP PER RUN OF SPEECH, AND IT USED TO BE ONE CLIP OVER THE WHOLE FILM.
# The file is continuous — a shot's speech sits at that shot's moment and the
# room between shots is silence — so a single clip spanning it drew a flat empty
# bar from 0:00 to the first word and another across every pause. Right to the
# millisecond, wrong to look at, and the user's first job on a paid run was
# razoring it into the pieces the server already knew the bounds of. Reported as
# "keep the voiceover wave only, not the blank part".
voice = [t for t in doc["audio_tracks"] if t["filename"] == "Voiceover.wav"]
track = voice[0]
check("ONE CLIP PER RUN OF SPEECH, not one bar across the whole film",
      len(voice), 2)
check("the first starts on the first word, at 0:00", voice[0]["start_ms"], 0)
check("it is a WAV named for what it is", track["filename"], "Voiceover.wav")
check("⚠ AND THE SECOND SITS ON THE SHOT IT IS READ OVER, not at 0:00 — the"
      " silence between them is a GAP now, not a flat bar",
      after["fr2"][0] <= voice[1]["start_ms"] < after["fr2"][1])
check("no clip carries silence: each is trimmed to its own speech",
      all(t.get("trim_ms") and t["trim_ms"] > 0 for t in voice))
check("...and none of them runs into the next",
      all(voice[i]["start_ms"] + voice[i]["trim_ms"] <= voice[i + 1]["start_ms"]
          for i in range(len(voice) - 1)))
check("each reads as far INTO the file as it sits along the timeline",
      all(t["offset_ms"] == t["start_ms"] for t in voice))
check("⚠ THEY ARE WINDOWS OF ONE UPLOAD, so Media lists one card",
      len({t["upload_id"] for t in voice}), 1)
check("...with an id each, because the editor keys a clip by id",
      len({t["id"] for t in voice}), 2)
caps = sorted(
    [t for t in doc["texts"] if t.get("layer_id") == "captions"],
    key=lambda t: t["start_ms"],
)



def caps_in(window):
    """The captions whose words are spoken inside this shot."""
    lo, hi = window
    return [c for c in caps if lo <= c["start_ms"] < hi]


# ⚠ A SPOKEN LINE IS SEVERAL CAPTIONS NOW, NOT ONE. `captions.MAX_WORDS` caps a
# caption at five words, so "Sit with me a while." comes back as two — the fix
# for a whole sentence sitting under the picture as one wall of text while it is
# read. What has to stay true is that every spoken shot carries its own words and
# no caption strays into a shot it was not read over, which is what this asserts
# instead of a count.
check("every spoken shot carries its caption, and no caption exceeds the word cap",
      len(caps_in(after["fr0"])) >= 1 and len(caps_in(after["fr2"])) >= 1
      and all(len(c["text"].split()) <= captions.MAX_WORDS for c in caps))
check("the first caption sits inside the shot it belongs to",
      after["fr0"][0] <= caps[0]["start_ms"] and caps[0]["start_ms"] < after["fr0"][1])
check("AND SO DOES THE SECOND — the bug, in one line",
      bool(caps_in(after["fr2"]))
      and all("found" in c["text"] for c in caps_in(after["fr2"])))
check("...and the pieces of one line stay inside the shot that line is read over",
      all(after["fr0"][0] <= c["start_ms"] < after["fr0"][1] for c in caps
          if "Sit" in c["text"] or "while" in c["text"]))
check("the caption is the WORDS, never the stage direction",
      all("Read this line as" not in c["text"] for c in caps))
check("...which the model was given, though",
      any("Read this line as an elderly man" in p for _v, p in PROMPTS))
check("A GRANDFATHER IS READ BY THE VOICE CAST FOR ONE, not by the dialog's Kore",
      PROMPTS[0][0], tts.PERSONAS["grandfather"]["voice"])
check("...and the girl by hers", PROMPTS[1][0], tts.PERSONAS["girl"]["voice"])


def wav_ms(path):
    with wave.open(path, "rb") as fh:
        return int(round(fh.getnframes() * 1000 / fh.getframerate()))


# ⚠ `config.OUTPUT_DIR`, NOT THE LITERAL "output". Hard-coding it worked only
# while this suite ran against the developer's own `.env` and wrote into the
# repo — which is the thing `_sandbox.pin` above stops. The server has always
# asked config for this path; so must anything checking what the server wrote.
media = os.path.join(config.OUTPUT_DIR, "_animatics", job_id, "media",
                     f"audio_{track['upload_id']}.wav")
check("the file really holds the audio the track claims",
      abs(wav_ms(media) - track["duration_ms"]) <= 1,
      True)

# ---------------------------------------------------------------------------
print("\n[5] a second run over its own output moves nothing")
before = spans(job_id)
run_voiceover(job_id, {"voice": "Kore", "add_captions": False})
check("EVERY CLIP IS WHERE IT WAS — the pass is not a re-lay of the row",
      spans(job_id), before)

# ---------------------------------------------------------------------------
print("\n[6] the edited sheet is what gets read")
job_id = new_animatic("Edited sheet")
PROMPTS.clear()
run_voiceover(job_id, {
    "voice": "Kore",
    "add_captions": True,
    "replace": True,
    "fit_shots": True,
    "lines": [
        {"frame_id": "fr2", "character": "PRIYA", "persona": "boy",
         "text": "Actually, I lost it. [edited]"},
    ],
})
doc = project(job_id)
caps = [t for t in doc["texts"] if t.get("layer_id") == "captions"]
check("only the line that was sent is read", len(caps), 1)
check("and it is the EDITED words, not the board's",
      caps[0]["text"], "Actually, I lost it. [edited]")
check("the board's own line was never sent to the model",
      any("[long]" in p for _v, p in PROMPTS), False)
check("a re-cast speaker is read by the new casting",
      PROMPTS[0][0], tts.PERSONAS["boy"]["voice"])
check("a short line leaves its shot exactly as long as it was",
      spans(job_id)["fr2"], (4000, 6000))

# ---------------------------------------------------------------------------
print("\n[7] fit_shots off: the pictures stay put and the AUDIO moves instead")
job_id = new_animatic("No fit")
run_voiceover(job_id, {"voice": "Kore", "add_captions": True, "fit_shots": False})
after = spans(job_id)
check("not one picture moved",
      after, {"fr0": (0, 2000), "fr1": (2000, 4000), "fr2": (4000, 6000),
              "fr3": (6000, 8000), "vr0": (4000, 9000)})
doc = project(job_id)
caps = sorted(
    [t for t in doc["texts"] if t.get("layer_id") == "captions"],
    key=lambda t: t["start_ms"],
)
# ⚠ THE SECOND LINE'S CAPTION, FOUND BY ITS WORDS RATHER THAN BY INDEX. The long
# line is several captions now (`captions.MAX_WORDS`), so `caps[1]` is its second
# PIECE — still part of the first line, and nowhere near the question being asked.
second = next(c for c in caps if "found" in c["text"])
check("the long line still cannot be spoken over by the next one",
      second["start_ms"] >= 9000 + GAP)

# ---------------------------------------------------------------------------
# The wiring. A source read, because these three are the ways the feature can be
# whole on the server and invisible (or actively wrong) in the browser.
# ---------------------------------------------------------------------------
print("\n[8] the browser half is wired to all of it")
from pathlib import Path  # noqa: E402 — used only by this section

ROOT = Path(__file__).resolve().parent.parent
editor = (ROOT / "client/src/components/AnimaticEditor.jsx").read_text(encoding="utf-8")
api_js = (ROOT / "client/src/api.js").read_text(encoding="utf-8")

check("the dialog fetches the sheet when it opens",
      "api.getAnimaticDialogue(animaticId)" in editor)
check("the edited lines are sent on BOTH calls, so the price is the price",
      api_js.count("voiceoverBody(opts)") == 2 and "fit_shots:" in api_js)
check("THE VOICE LIST IS NOT TYPED INTO THE JSX ANY MORE",
      '["Kore", "Puck", "Charon", "Zephyr", "Fenrir", "Aoede"]' in editor, False)
check("...it comes from the sheet the server sent",
      "speechSheet?.voices" in editor)
# ⚠ THE ONE THAT ROTS SILENTLY. The pass moves PICTURES now, and an editor that
# re-read only the captions and the audio would hold the old layout — its next
# autosave then writes that back over the one the server just worked out, and
# every line is over the wrong shot again with nothing on screen to explain it.
check("the poll re-reads the FRAMES, not just the texts and the audio",
      "const laid = project.frames || [];" in editor
      and "setFrames(rippleFrames(laid, shifts));" in editor)
# ⚠ AND CARRIES EVERYTHING THE SERVER DIDN'T RE-TIME. The run re-lays the board's
# row and writes its own captions and its own voiceover; typed text, shapes,
# overlays, the Video row and a music bed are left where they were, and one shot
# growing puts all of them out for the rest of the film. Same `ripple.js` the Veo
# attach uses — see `tests/timeline_ripple_check.py`.
# ⚠ THE RIPPLE MOVED INTO `absorbSpeech` (2026-08-23, the Director's phase B) and
# the assertion moved with it. It is ONE function with TWO callers now — the 🎙
# dialog's poll and the Director's sound pass — which is the point: this
# arithmetic is the part nobody can check by eye, and a second copy of it
# drifting is how "my caption and voiceover not move" comes back.
check("…and carries the rest of the timeline along with them",
      "const shifts = renderShifts(beforeFrames || [], laid);" in editor
      and "setShapes((list) => rippleClips(list, shifts));" in editor)
check("…and the pre-run picture row is what the map is built from",
      "absorbSpeech(project, speechFramesRef.current, speechAudioRef.current)" in editor)
check("⚠ …and the Director's pass goes through the SAME re-read, not a second copy",
      "return { frames: absorbSpeech(project, before, beforeAudio) };" in editor
      and editor.count("const absorbSpeech = useCallback(") == 1)
# ⚠ `keep` IS NOT OPTIONAL. The generated captions and the new voiceover are
# already laid against the NEW layout; shifting them by the same map would move
# them twice, which is this very bug committed by its own fix.
check("…without moving the captions and voiceover it just wrote a second time",
      "filter(isGeneratedCaption)" in editor
      and "rippleClips(project.texts || [], shifts, keep)" in editor)

print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("A shot holds its own line, the take travels with it, and the sheet is the script.")
