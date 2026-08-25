"""PHASES D AND E — the sound effects and the music bed, and WHERE THEY LAND.

    python tests/director_sound_check.py

⚠ THE PROPERTY THIS EXISTS FOR IS THE ORDER, AND IT IS THE MIRROR IMAGE OF
`director_voice_order_check.py`. That test asserts the voiceover runs BEFORE the
steps, because it moves the pictures the steps decide about. This one asserts the
sound cues are placed AFTER them, because a cue lands on a MOMENT — "the door
slams as shot 9 begins" — and the steps spend six seconds rewriting where shot 9
begins (`set_shot_duration`, `set_all_durations`).

And it fails the same INVISIBLE way: every clip is placed, the run reports "11
sound effects added", every one of them is on a real frame. They are simply the
wrong frames, and the only way anybody finds out is by watching the film and
feeling that the slams are slightly off. There is no exception and no dropped
step — which is exactly the kind of failure that has to be tested on purpose.

Six things are checked, in order of how much they matter:

  1. THE ORDER PROPERTY. A film whose shots the EDIT re-times: cue on shot 4,
     which starts at 6s before the steps run and at 12s after. The placement that
     ships must be 12s. The test is written so the naive implementation — place
     the sound with the rest of the plan — fails it, and it is checked that it does.
  2. THE MUSIC LOOP. A 20s bed under a 70s film is four clips, back to back, of
     ONE file; the last one is TRIMMED so the fade-out lands on the final frame,
     and only the first and last carry a fade at all.
  3. THE LEVEL. The bed sits far lower under a voiceover than on a silent film,
     and the number is on the CLIP — because the export mixes clips and a level
     that only existed in the browser would be a preview that lies about the mp4.
  4. THE BUDGET. Repeating a cue's wording is ONE download and several clips;
     asking for more DISTINCT sounds than the shared library budget allows is
     refused with a reason, and the repeat is never what gets refused.
  5. THE DUE RULES. Both passes answer with a REASON, and the rules-only planner
     (no reading at all) is a "no" with an explanation rather than an empty list.
  6. THE PYTHON SIDE. The reading carries `sfx` and `music`; a cue in the wrong
     script is dropped with a reason before anything is searched for, because the
     sound library is indexed in English and would find nothing.
  7. ⚠ THE SEARCH LADDER, WHICH IS WHY A FILM GOT NO MUSIC AT ALL. Reported from
     the screen: "ambient peaceful piano underscore" and "light feather rustle"
     both came back with zero CC0 results, so a plan promising two effects and a
     score delivered one effect and silence. The library matches EVERY word, so a
     model one adjective over the line is a silent shot. Each cue now gets a
     second, WIDER ask — its last two words, with the length filter dropped
     entirely — and the widening is REPORTED rather than done quietly.

Needs node for 1-5. Nothing here touches a browser, a backend, a model, a
Freesound key or a dollar — the "import" is a dict, exactly as the server's answer
would be.
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
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


HARNESS = """
import {
  MAX_SFX_SOUNDS,
  MUSIC_MIN_SECONDS,
  SFX_MAX_SECONDS,
  MUSIC_FADE_IN_MS,
  MUSIC_FADE_OUT_MS,
  MUSIC_VOLUME_ALONE,
  MUSIC_VOLUME_UNDER_SPEECH,
  SFX_VOLUME,
  cueKey,
  musicCue,
  musicDue,
  musicPlacement,
  scoreReport,
  sfxCues,
  sfxDue,
  sfxPlacements,
  soundtrackRequest,
} from "__SOUND__";

/** A timeline, `starts` laid end to end the way `readCtx` builds it. */
function timeline(lengths) {
  const frames = [];
  const starts = [];
  let at = 0;
  lengths.forEach((ms, i) => {
    frames.push({ id: `f${i + 1}`, duration_ms: ms, label: `Shot ${i + 1}` });
    starts.push(at);
    at += ms;
  });
  return { frames, starts, totalMs: at };
}

/** What the server answers with, for cues it found something for. */
function filed(rows) {
  return rows.map((r, i) => ({
    key: r.key,
    query: r.query || r.key,
    upload_id: `u${i + 1}`,
    filename: `${r.key}.mp3`,
    duration_ms: r.ms === undefined ? 2000 : r.ms,
    attribution: "",
  }));
}

const out = {};

// =========================================================================
// 1. THE ORDER PROPERTY
// =========================================================================
// Six shots of 3s. A cue sits on shot 4, which begins at 9s. The EDIT then
// re-times shots 1-3 to 6s each, so shot 4 begins at 18s. The cue has to move
// with it, and the only way it can is by the cues being read again AFTER the
// steps — which is what the `scoring` phase does.
const before = timeline([3000, 3000, 3000, 3000, 3000, 3000]);
const after = timeline([6000, 6000, 6000, 3000, 3000, 3000]);
const reading = {
  shots: [
    { shot: 1, sfx: "" },
    { shot: 4, sfx: "heavy door slam" },
  ],
  music: { query: "slow melancholic piano underscore", mood: "elegiac" },
};

const cuesBefore = sfxCues({ analysis: reading, frames: before.frames, starts: before.starts });
const cuesAfter = sfxCues({ analysis: reading, frames: after.frames, starts: after.starts });
const importedSfx = filed([{ key: cueKey("heavy door slam") }]);
out.order = {
  beforeAt: cuesBefore.cues.map((c) => c.at_ms),
  afterAt: cuesAfter.cues.map((c) => c.at_ms),
  // What actually gets laid down when the placement is computed after the steps.
  shipped: sfxPlacements({ cues: cuesAfter.cues, imported: importedSfx }).clips.map((c) => ({
    start: c.start_ms,
    volume: c.volume,
    upload: c.upload_id,
  })),
  // ...and what a naive "place it with the plan" build would have shipped.
  naive: sfxPlacements({ cues: cuesBefore.cues, imported: importedSfx }).clips.map(
    (c) => c.start_ms
  ),
  sfxVolume: SFX_VOLUME,
};

// =========================================================================
// 2 & 3. THE MUSIC LOOP AND ITS LEVEL
// =========================================================================
// A 20s bed under a 70s film: 0, 20, 40, 60 — and the last clip has 10s of room,
// so it is trimmed to 10s and its fade-out lands on the final frame.
const bed = musicCue({ analysis: reading });
const bedFile = filed([{ key: bed.key, ms: 20000 }]);
const loop = musicPlacement({ cue: bed, imported: bedFile, totalMs: 70000, underSpeech: false });
out.loop = {
  clips: loop.clips.map((c) => ({
    start: c.start_ms,
    trim: c.trim_ms,
    fin: c.fade_in_ms,
    fout: c.fade_out_ms,
    upload: c.upload_id,
    volume: c.volume,
  })),
  uploads: [...new Set(loop.clips.map((c) => c.upload_id))].length,
};
// An exact fit: 60s film over a 20s bed is three clips and NO trim on any of them.
out.exact = musicPlacement({
  cue: bed,
  imported: bedFile,
  totalMs: 60000,
  underSpeech: false,
}).clips.map((c) => ({ start: c.start_ms, trim: c.trim_ms }));
// Under a voiceover.
out.ducked = musicPlacement({
  cue: bed,
  imported: bedFile,
  totalMs: 70000,
  underSpeech: true,
}).clips.map((c) => c.volume);
out.levels = { alone: MUSIC_VOLUME_ALONE, under: MUSIC_VOLUME_UNDER_SPEECH };
out.fades = { in: MUSIC_FADE_IN_MS, out: MUSIC_FADE_OUT_MS };
// A file whose length nobody knows is laid ONCE, never looped forever.
out.unknown = musicPlacement({
  cue: bed,
  imported: filed([{ key: bed.key, ms: 0 }]),
  totalMs: 70000,
  underSpeech: false,
}).clips.length;
// Nothing found for it: no clips, and a reason.
out.bedMissing = musicPlacement({ cue: bed, imported: [], totalMs: 70000 });

// =========================================================================
// 4. THE BUDGET — distinct sounds cost, repeats do not
// =========================================================================
// Twelve shots. Shots 1-3 all cue the SAME wording (differently punctuated), and
// shots 4-14 each cue something new — so the cap bites on the new ones and never
// on the repeats.
const many = timeline(Array(14).fill(2000));
const manyReading = {
  shots: [
    { shot: 1, sfx: "Footsteps on gravel" },
    { shot: 2, sfx: "footsteps on gravel" },
    { shot: 3, sfx: "footsteps on gravel." },
    ...Array.from({ length: 11 }, (_, i) => ({ shot: i + 4, sfx: `unique sound ${i + 1}` })),
  ],
  music: { query: "" },
};
const budget = sfxCues({ analysis: manyReading, frames: many.frames, starts: many.starts });
out.budget = {
  cues: budget.cues.length,
  sounds: budget.sounds.length,
  cap: MAX_SFX_SOUNDS,
  // The three repeats must all have made it, and share one key.
  gravel: budget.cues.filter((c) => c.key === cueKey("footsteps on gravel")).length,
  keys: [...new Set(budget.cues.map((c) => c.key))].length,
  skipped: budget.skipped.map((s) => ({ shot: s.shot, query: s.query })),
};
// One request, one row per DISTINCT sound, plus the bed when there is one.
// ⚠ AND THE LENGTH BOUNDS ARE WIDE ENOUGH TO BE TRUE RATHER THAN NARROW ENOUGH
// TO BE A FILTER. 8s for an effect and a 12s FLOOR for the bed were half of why
// two cues came back empty; nothing downstream needs either (a short bed LOOPS, a
// long effect is clamped by `trackPlayMs`).
out.bounds = { sfxMax: SFX_MAX_SECONDS, musicMin: MUSIC_MIN_SECONDS };
out.request = {
  withBed: soundtrackRequest({ sounds: budget.sounds, music: bed }).sounds.length,
  noBed: soundtrackRequest({ sounds: budget.sounds, music: null }).sounds.length,
  kinds: soundtrackRequest({ sounds: budget.sounds, music: bed }).sounds.map((s) => s.kind),
  nothing: soundtrackRequest({ sounds: [], music: null }),
};
// A cue over a shot that is not on this timeline.
out.offEnd = sfxCues({
  analysis: { shots: [{ shot: 99, sfx: "thunder" }] },
  frames: before.frames,
  starts: before.starts,
});
// Three clips of one recording: the placement reuses the upload, it does not
// fetch three.
const shared = sfxPlacements({
  cues: budget.cues.filter((c) => c.key === cueKey("footsteps on gravel")),
  imported: filed([{ key: cueKey("footsteps on gravel") }]),
});
out.shared = {
  clips: shared.clips.length,
  uploads: [...new Set(shared.clips.map((c) => c.upload_id))].length,
  starts: shared.clips.map((c) => c.start_ms),
};
// A cue nothing was found for is NAMED, not silently dropped.
out.notFound = sfxPlacements({ cues: cuesAfter.cues, imported: [] });

// =========================================================================
// 5. THE DUE RULES
// =========================================================================
const noReading = sfxCues({ analysis: null, frames: before.frames, starts: before.starts });
out.due = {
  sfxOn: sfxDue({ sfx: true }, cuesAfter.sounds),
  sfxOff: sfxDue({ sfx: false }, cuesAfter.sounds),
  sfxHouse: sfxDue({ sfx: true }, noReading.sounds),
  musicOn: musicDue({ music: true }, bed),
  musicOff: musicDue({ music: false }, bed),
  musicHouse: musicDue({ music: true }, musicCue({ analysis: null })),
  musicEmpty: musicDue({ music: true }, musicCue({ analysis: { music: { query: "  " } } })),
};
out.report = {
  both: scoreReport({ sfx: shared.clips, music: loop.clips, sfxMissing: [] }),
  // One of the effects only matched a shortened query — the report has to say so.
  widened: scoreReport({
    sfx: shared.clips.map((c, i) => (i ? c : { ...c, relaxedTo: "feather rustle" })),
    music: [],
    sfxMissing: [],
  }),
  lost: scoreReport({
    sfx: shared.clips,
    music: [],
    sfxMissing: [{ shot: 4, query: "thunder" }],
  }),
  nothing: scoreReport({ sfx: [], music: [], sfxMissing: [] }),
};

process.stdout.write(JSON.stringify(out));
"""


def run_node():
    work = tempfile.mkdtemp(prefix="dir-sound-")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(HARNESS.replace("__SOUND__", (AGENT / "sound_pass.js").as_uri()))
        proc = subprocess.run(
            ["node", harness],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            print("    node said:", (proc.stderr or "").strip()[:1500])
            return None
        return json.loads(proc.stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check_js(data):
    print("\n⚠ THE ORDER PROPERTY — the EDIT moves the shots, so the cues are timed\n"
          "  AFTER it. This is the whole reason phases D and E run last.\n")
    o = data["order"]
    check("before the edit, the cue on shot 4 sits at 9s",
          o["beforeAt"] == [9000], json.dumps(o["beforeAt"]))
    check("after it re-times shots 1-3, shot 4 begins at 18s",
          o["afterAt"] == [18000], json.dumps(o["afterAt"]))
    check("⚠ what SHIPS is the 18s placement, not the 9s one",
          [c["start"] for c in o["shipped"]] == [18000], json.dumps(o["shipped"]))
    check("...and the naive 'place it with the plan' build would have shipped 9s",
          o["naive"] == [9000], json.dumps(o["naive"]))
    check("the effect carries its own level, not 1.0",
          all(c["volume"] == o["sfxVolume"] for c in o["shipped"]) and 0 < o["sfxVolume"] < 1,
          str(o["sfxVolume"]))

    print("\n⚠ THE MUSIC LOOP — several clips of ONE file, because there is no loop\n"
          "  flag on an audio clip and there must not be one.\n")
    loop = data["loop"]
    check("a 20s bed under a 70s film is four clips",
          len(loop["clips"]) == 4, json.dumps(loop["clips"]))
    check("...back to back, at 0 / 20 / 40 / 60s",
          [c["start"] for c in loop["clips"]] == [0, 20000, 40000, 60000],
          json.dumps([c["start"] for c in loop["clips"]]))
    check("...all of ONE upload, so it is one file against the audio cap",
          loop["uploads"] == 1, str(loop["uploads"]))
    check("⚠ the LAST clip is trimmed to the 10s of film left",
          loop["clips"][-1]["trim"] == 10000, json.dumps(loop["clips"][-1]))
    check("...and no other clip is trimmed at all",
          [c["trim"] for c in loop["clips"][:-1]] == [0, 0, 0],
          json.dumps([c["trim"] for c in loop["clips"]]))
    check("the bed fades IN once, on the first clip only",
          [c["fin"] for c in loop["clips"]] == [data["fades"]["in"], 0, 0, 0],
          json.dumps([c["fin"] for c in loop["clips"]]))
    check("...and OUT once, on the last clip only",
          [c["fout"] for c in loop["clips"]] == [0, 0, 0, data["fades"]["out"]],
          json.dumps([c["fout"] for c in loop["clips"]]))
    check("an exact fit needs no trim on any clip",
          data["exact"] == [{"start": 0, "trim": 0}, {"start": 20000, "trim": 0},
                            {"start": 40000, "trim": 0}],
          json.dumps(data["exact"]))
    check("a file of unknown length is laid ONCE, never looped forever",
          data["unknown"] == 1, str(data["unknown"]))
    check("nothing found for the bed is no clips and a reason",
          not data["bedMissing"]["clips"] and bool(data["bedMissing"]["why"]),
          json.dumps(data["bedMissing"]))

    print("\n⚠ THE LEVEL IS ON THE CLIP, because the EXPORT mixes clips — a level\n"
          "  that only existed in the browser would be a preview that lies.\n")
    check("on a silent film the bed plays at its own level",
          all(v == data["levels"]["alone"] for v in
              [c["volume"] for c in loop["clips"]]),
          json.dumps([c["volume"] for c in loop["clips"]]))
    check("⚠ under a voiceover it is far lower",
          all(v == data["levels"]["under"] for v in data["ducked"])
          and data["levels"]["under"] < data["levels"]["alone"] / 2,
          json.dumps(data["levels"]))

    print("\n⚠ THE BUDGET IS DISTINCT SOUNDS, NOT CLIPS — the shared library allows\n"
          "  60 requests a minute for the whole deployment.\n")
    b = data["budget"]
    check(f"no more than {b['cap']} distinct sounds are fetched",
          b["sounds"] == b["cap"], str(b["sounds"]))
    check("the three differently-punctuated repeats are ONE sound",
          b["gravel"] == 3 and b["keys"] == b["cap"],
          f"gravel={b['gravel']} keys={b['keys']}")
    check("...and a repeat is never what gets refused",
          all("gravel" not in (s["query"] or "") for s in b["skipped"]),
          json.dumps(b["skipped"]))
    check("everything over the cap is skipped WITH A REASON, not dropped",
          len(b["skipped"]) == 14 - b["cues"] and all(s["shot"] for s in b["skipped"]),
          json.dumps(b["skipped"]))
    r = data["request"]
    check("one request row per distinct sound, plus the bed",
          r["withBed"] == b["cap"] + 1 and r["noBed"] == b["cap"],
          f"{r['withBed']} / {r['noBed']}")
    check("...and the bed is the one row marked 'music'",
          r["kinds"].count("music") == 1 and r["kinds"][-1] == "music",
          json.dumps(r["kinds"]))
    check("nothing cued is no request at all, not an empty one",
          r["nothing"] is None, json.dumps(r["nothing"]))
    check("⚠ the length bounds are wide, because a narrow one made a film silent",
          data["bounds"]["sfxMax"] >= 20 and data["bounds"]["musicMin"] <= 6,
          json.dumps(data["bounds"]))
    check("a cue over a shot that does not exist is skipped with a reason",
          not data["offEnd"]["cues"] and len(data["offEnd"]["skipped"]) == 1
          and "99" in data["offEnd"]["skipped"][0]["why"],
          json.dumps(data["offEnd"]))
    sh = data["shared"]
    check("three shots cueing one sound is three clips of ONE upload",
          sh["clips"] == 3 and sh["uploads"] == 1, json.dumps(sh))
    check("...each at its own shot's start",
          sh["starts"] == [0, 2000, 4000], json.dumps(sh["starts"]))
    nf = data["notFound"]
    check("a cue nothing was found for is NAMED, never silently dropped",
          not nf["clips"] and len(nf["missing"]) == 1 and nf["missing"][0]["shot"] == 4,
          json.dumps(nf))

    print("\n⚠ EVERY 'NO' IS A REASON, because the panel prints it verbatim under\n"
          "  the tick box — a switch that changes nothing has to say why.\n")
    d = data["due"]
    check("cued and ticked: due", d["sfxOn"]["due"] and not d["sfxOn"]["why"])
    check("un-ticked: not due, and it says so",
          not d["sfxOff"]["due"] and "switched off" in d["sfxOff"]["why"],
          d["sfxOff"]["why"])
    check("⚠ the rules planner writes no cues, and the reason says so",
          not d["sfxHouse"]["due"] and "rhythm" in d["sfxHouse"]["why"],
          d["sfxHouse"]["why"])
    check("music cued and ticked: due", d["musicOn"]["due"])
    check("music un-ticked: not due, with a reason",
          not d["musicOff"]["due"] and "switched off" in d["musicOff"]["why"])
    check("no reading means no bed, with a reason rather than a crash",
          not d["musicHouse"]["due"] and bool(d["musicHouse"]["why"]))
    check("a blank music query is 'no music', not a search for nothing",
          not d["musicEmpty"]["due"], json.dumps(d["musicEmpty"]))

    rep = data["report"]
    check("the report counts CLIPS and RECORDINGS separately",
          "3 sound effects from 1 recording" in rep["both"], rep["both"])
    check("...and says the bed was looped rather than pretending it was one clip",
          "looped 4 times" in rep["both"], rep["both"])
    check("⚠ a cue that found nothing is in the sentence, not left out of it",
          "1 cue found nothing usable" in rep["lost"], rep["lost"])
    check("nothing placed says nothing was added",
          "No sound was added" in rep["nothing"], rep["nothing"])
    check("⚠ a sound found on a wider search is counted in the sentence",
          "found on a wider search" in rep["widened"], rep["widened"])
    check("...and a run where nothing was widened does not mention it",
          "wider search" not in rep["both"], rep["both"])


def check_ladder():
    """The two attempts a cue gets, and what the route does with them.

    ⚠ NO NETWORK AND NO KEY. `freesound.search` is replaced by a function that
    records what it was asked and answers only for the queries a real library
    would have had something for — which is the only way to assert "it tried
    again with fewer words" without depending on somebody else's catalogue.
    """
    print("\n⚠ THE SEARCH LADDER — a four-word cue is asked again with two.\n")
    import freesound
    import server.animatics as A
    from server.schemas import Job, JobKind, JobStatus, SoundCueRequest, SoundtrackRequest

    # --- the ladder itself, before any route runs ---------------------------
    ladder = A._cue_attempts("ambient peaceful piano underscore", 0, 5)
    check("a four-word cue gets two attempts, not one",
          len(ladder) == 2, json.dumps(ladder))
    check("the first asks for it as written, with the length filter on",
          ladder[0]["query"] == "ambient peaceful piano underscore"
          and ladder[0]["min_seconds"] == 5 and not ladder[0]["relaxed"],
          json.dumps(ladder[0]))
    check("⚠ the second asks for the LAST TWO WORDS — English puts the noun last",
          ladder[1]["query"] == "piano underscore", json.dumps(ladder[1]))
    check("...and drops the length filter entirely",
          ladder[1]["min_seconds"] == 0 and ladder[1]["max_seconds"] == 0,
          json.dumps(ladder[1]))
    check("...and says what it widened to, for the panel to print",
          ladder[1]["relaxed"] == "piano underscore", ladder[1]["relaxed"])
    two = A._cue_attempts("door slam", 30, 0)
    check("a two-word cue widens to one word plus no length filter",
          len(two) == 2 and two[1]["query"] == "slam" and two[1]["max_seconds"] == 0,
          json.dumps(two))
    one = A._cue_attempts("thunder", 30, 0)
    check("a ONE-word cue is not shortened — only its length filter goes",
          len(one) == 2 and one[1]["query"] == "thunder"
          and one[1]["relaxed"] == "any length",
          json.dumps(one))
    check("...and an attempt identical to one already made is never repeated",
          len(A._cue_attempts("x", 0, 0)) == 1, json.dumps(A._cue_attempts("x", 0, 0)))

    # --- the route driving it ----------------------------------------------
    asked = []

    def fake_search(query="", licence="safe", min_seconds=0, max_seconds=0, **kw):
        asked.append({"q": query, "licence": licence,
                      "min": min_seconds, "max": max_seconds})
        # The library only has something for the SHORTENED music query and for
        # the effect as written — exactly the shape the user hit.
        if query in ("piano underscore", "wind chimes"):
            return {"items": [{"id": "1", "name": query, "preview_url": "http://x/y.mp3",
                               "duration_ms": 9000, "license": "cc0", "attribution": "",
                               "license_label": "CC0", "needs_credit": False,
                               "page_url": "p"}]}
        return {"items": []}

    was_search, was_download, was_conf = freesound.search, freesound.download, freesound.configured
    was_owned, was_media = A._get_owned_animatic, A._media_dir
    was_files = A._audio_files_of
    try:
        freesound.search = fake_search
        freesound.download = lambda item, cap: (b"ID3", "%s.mp3" % item["name"])
        freesound.configured = lambda: True
        job = Job(job_id="jl", kind=JobKind.ANIMATIC, status=JobStatus.SUCCEEDED,
                  character_name="x", created_at="2026-08-25T00:00:00Z",
                  updated_at="2026-08-25T00:00:00Z")
        A._get_owned_animatic = lambda jid, cur: job
        A._audio_files_of = lambda j: set()
        work = tempfile.mkdtemp(prefix="ladder-")
        A._media_dir = lambda jid: os.path.join(work, jid)

        res = A.build_soundtrack(
            "jl",
            SoundtrackRequest(sounds=[
                SoundCueRequest(key="wind chimes", query="wind chimes",
                                kind="sfx", max_seconds=30),
                SoundCueRequest(key="ambient peaceful piano underscore",
                                query="ambient peaceful piano underscore",
                                kind="music", min_seconds=5),
            ]),
            current=None,
        )
    finally:
        freesound.search, freesound.download, freesound.configured = (
            was_search, was_download, was_conf)
        A._get_owned_animatic, A._media_dir, A._audio_files_of = (
            was_owned, was_media, was_files)
        shutil.rmtree(work, ignore_errors=True)

    check("the cue that matched as written cost ONE request",
          [a["q"] for a in asked].count("wind chimes") == 1,
          json.dumps([a["q"] for a in asked]))
    check("⚠ THE FOUR-WORD MUSIC CUE WAS ASKED AGAIN, SHORTER, AND FOUND",
          "piano underscore" in [a["q"] for a in asked]
          and len(res.items) == 2,
          json.dumps([a["q"] for a in asked]))
    check("...so the film gets its music instead of playing dry",
          any(i.kind == "music" for i in res.items),
          json.dumps([(i.kind, i.query) for i in res.items]))
    music = [i for i in res.items if i.kind == "music"][0]
    check("⚠ and the widening is REPORTED, not done quietly",
          music.relaxed_to == "piano underscore", music.relaxed_to)
    sfx = [i for i in res.items if i.kind == "sfx"][0]
    check("...while a cue found as written reports no widening at all",
          sfx.relaxed_to == "", repr(sfx.relaxed_to))
    check("⚠ THE LICENCE FENCE IS NEVER WHAT GETS RELAXED",
          all(a["licence"] == "safe" for a in asked),
          json.dumps([a["licence"] for a in asked]))
    check("...and the LENGTH filter is what does relax, on the second ask only",
          any(a["min"] == 5 for a in asked) and any(a["min"] == 0 for a in asked),
          json.dumps([(a["q"], a["min"], a["max"]) for a in asked]))
    check("nothing was skipped, because everything was eventually found",
          not res.skipped, json.dumps(res.skipped))


def check_python():
    print("\n⚠ THE READING CARRIES THE CUES, and a cue in the wrong script is\n"
          "  dropped BEFORE anything is searched for — the library is English.\n")
    import director

    schema = director.analyse_schema()
    shot = schema["properties"]["shots"]["items"]["properties"]
    check("the analyse schema asks for `sfx` per shot", "sfx" in shot,
          json.dumps(sorted(shot)))
    check("...and for one `music` object for the film",
          "music" in schema["properties"]
          and "query" in schema["properties"]["music"]["properties"],
          json.dumps(sorted(schema["properties"])))

    instruction = director.sound_instruction()
    check("the prompt says the cues are SEARCH TERMS",
          "SEARCH TERMS" in instruction, instruction[:80])
    check("...that most shots get nothing",
          "MOST SHOTS GET" in instruction)
    check("...that there is ONE bed and not one per scene",
          "not one per scene" in instruction)
    check("...and that both fields are English in every language",
          "ENGLISH IN EVERY LANGUAGE" in instruction)
    check("⚠ and the analyse template has a slot for it",
          "<<SOUND>>" in director.prompts()["analyse"])

    brief = {"shot_count": 3}
    read = director._coerce_analysis(
        {
            "logline": "x",
            "mood": "tense",
            "shots": [
                {"shot": 1, "beat": "a", "sfx": "heavy door slam"},
                {"shot": 2, "beat": "b", "sfx": "दरवाज़ा बंद"},
                {"shot": 3, "beat": "c"},
            ],
            "music": {"query": "slow piano underscore", "mood": "elegiac"},
        },
        brief,
    )
    check("a cue comes through the coercion intact",
          read["shots"][0]["sfx"] == "heavy door slam", read["shots"][0]["sfx"])
    check("a shot with no cue gets \"\", not a missing key",
          read["shots"][2]["sfx"] == "", repr(read["shots"][2].get("sfx")))
    check("the music object is always present",
          read["music"]["query"] == "slow piano underscore", json.dumps(read["music"]))

    fenced, dropped = director.enforce_sound_language(read)
    check("⚠ the Devanagari cue is dropped",
          fenced["shots"][1]["sfx"] == "", repr(fenced["shots"][1]["sfx"]))
    check("...with a reason naming the shot and saying why",
          len(dropped) == 1 and dropped[0]["verb"] == "sfx_cue"
          and dropped[0]["index"] == 2 and "English" in dropped[0]["why"],
          json.dumps(dropped))
    check("...and the English cue beside it is untouched",
          fenced["shots"][0]["sfx"] == "heavy door slam")
    check("a Latin cue with an accent is NOT dropped",
          director.in_script("café door slam", "latin"))

    hindi_bed, bed_dropped = director.enforce_sound_language(
        {**read, "music": {"query": "धीमा पियानो", "mood": "", "why": ""}}
    )
    check("a Devanagari music cue is dropped too",
          hindi_bed["music"]["query"] == "" and any(
              d["verb"] == "music_cue" for d in bed_dropped),
          json.dumps(bed_dropped))

    # A missing `music` key on an old reading must not raise.
    empty, _ = director.enforce_sound_language({"shots": []})
    check("a reading with no music key at all is handled, not raised",
          empty["music"] == {}, json.dumps(empty))

    print("\n⚠ AND NOTHING IN THE PLANNER FETCHES ANYTHING. The cues are text on a\n"
          "  plan the user reads before pressing anything.\n")
    import inspect

    source = inspect.getsource(director)
    check("director.py imports no HTTP client",
          "import requests" not in source and "urlopen" not in source)
    check("...and never mentions the sound provider",
          "freesound" not in source.lower())


def main():
    print("\n=== PHASES D AND E: the soundtrack ===")
    data = run_node()
    if data is None:
        print("  node is not on PATH, or sound_pass.js would not load — JS half skipped.")
        failures.append("node harness")
    else:
        check_js(data)
    check_ladder()
    check_python()

    print("\n" + "-" * 70)
    if failures:
        print(f"{len(failures)} FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
