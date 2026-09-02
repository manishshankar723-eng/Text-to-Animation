"""THE VEO PASS ADDS UP — 48 shots is four submissions, and four numbers that sum
to the one the user agreed to.

    python tests/director_chunk_check.py

⚠ THIS IS A TEST ABOUT MONEY, WHICH IS WHY IT IS PEDANTIC ABOUT A PENNY. Phase C
renders every shot in the film; a 48-shot board at the project's defaults is tens
of dollars. The user is shown ONE total before they press the button and then
watches four passes go by on the rail, and if those four do not add up to the
number they agreed to, the honest reading is that neither can be trusted. That
identity is not free: every quote is rounded to the penny, and rounding a shot
list once gives a different answer from rounding four twelfths of it and adding.
So `_quote_veo_run` computes the total AS the sum of the passes, and this asserts
it stays that way.

Five things are checked, in order of how much they matter:

  1. THE CHUNKING. 48 shots at a cap of 12 is exactly four passes, none longer
     than the cap, none empty, no shot lost and none rendered twice. The cap is
     `config.MAX_VIDEO_BATCH` — a SPEND guard an operator may change — so the
     arithmetic is checked at several caps rather than at the one this install
     happens to be set to.
  2. THE MONEY. `total.usd == sum(pass.usd)`, exactly, at 2dp; and the same for
     the shot and second counts.
  3. THE LENGTH POLICY. The smallest of 4/6/8 that COVERS the hold, never the
     nearest — a take shorter than its shot ends on a drawing, mid-scene, which
     reads as a bug. And a hold longer than 8s keeps its own length.
  4. THE MIXED BATCH REACHES THE SERVER. One submission carries a 4-second take
     and an 8-second one, `/animate/estimate` prices the mixture rather than
     `count × duration`, and each record is written with its own length. This is
     the half that would silently render everything at 8s.
  5. A TAKE IS NOT A SHOT. `shotRow` takes the renders out of the film the
     Director counts, or a second 🎬 run reads a 96-shot film that does not exist.

Needs node for the pure half. The money half runs against the real routes with
the render pool stubbed, so ⚠ VEO IS NEVER CALLED and this suite spends nothing.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ⚠ **EVERY STORE PINNED INTO A THROWAWAY DIRECTORY, BEFORE ANY `server.*`
# IMPORT.** `server/config.py` reads the environment once, at import time, so
# without this line the suite boots against the developer's real `.env` — it
# registers its test accounts in the production database and spends real monthly
# quota, and then fails when billing refuses it. G13; see `tests/_sandbox.py`.
from _sandbox import pin  # noqa: E402

_TMP = pin("director_chunk_check_")

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "client/src/animatic/agent"

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# ===========================================================================
# THE PURE HALF — chunking, the length policy, and what is refused
# ===========================================================================
HARNESS = """
import {
  VEO_LENGTHS,
  chunkPasses,
  coverSeconds,
  isTake,
  shotRow,
  veoDue,
  veoShots,
} from "__VEO__";

/** A picture row. `takes` names shots that also carry a Veo render. */
function timeline(lengths, takes = []) {
  const frames = [];
  const starts = [];
  let at = 0;
  lengths.forEach((ms, i) => {
    frames.push({
      id: `f${i + 1}`,
      kind: "image",
      duration_ms: ms,
      label: `Shot ${i + 1}`,
      src: { kind: "panel", storyboard_id: "b1", index: i },
    });
    starts.push(at);
    at += ms;
  });
  // A take is appended at the END of the list, which is what `attachVeoClip`
  // does — it is on another ROW, so it never sits between two panels.
  for (const n of takes) {
    frames.push({
      id: `t${n}`,
      kind: "video",
      duration_ms: 8000,
      label: `Shot ${n}`,
      src: { kind: "video", storyboard_id: "b1", index: n - 1, upload_id: `up${n}` },
    });
    starts.push(starts[n - 1]);
  }
  return { frames, starts };
}

const out = {};

// ------------------------------------------------------------ 1. the chunking
const many = Array.from({ length: 48 }, (_, i) => ({ shot: i + 1, id: `f${i + 1}` }));
out.chunk = {};
for (const cap of [12, 6, 1, 48, 50]) {
  const passes = chunkPasses(many, cap);
  out.chunk[cap] = {
    passes: passes.length,
    sizes: passes.map((p) => p.length),
    // Every shot, once, in film order — flattening must reproduce the input.
    flat: passes.flat().map((s) => s.shot),
  };
}
out.chunk.empty = chunkPasses([], 12).length;
// ⚠ A CAP OF ZERO OR NONSENSE MUST NOT LOOP FOR EVER. It is read off a server
// config an operator can set, so it is clamped rather than trusted.
out.chunk.zero = chunkPasses(many, 0).length;
out.chunk.junk = chunkPasses(many, "x").length;

// ------------------------------------------------------ 2. the length policy
out.lengths = VEO_LENGTHS;
out.cover = {};
for (const ms of [0, 500, 2400, 3999, 4000, 4001, 4060, 4061, 5000, 6000, 6060, 6061, 8000, 9300, 60000]) {
  out.cover[ms] = coverSeconds(ms);
}

// ------------------------------------------------ 3. what phase C refuses
const film = timeline([2400, 5000, 9300, 2000], [4]);
out.shots = veoShots({
  veo: [
    { shot: 1, prompt: "he turns to camera", dialogue: "" },
    { shot: 2, prompt: "the leaves fall", dialogue: "" },
    { shot: 3, prompt: "  ", dialogue: "she speaks" },
    { shot: 4, prompt: "the door closes", dialogue: "" },
    { shot: 9, prompt: "a shot that is not there", dialogue: "" },
  ],
  frames: shotRow(film.frames, film.starts).frames,
  // Shot 2 has already been rendered and paid for.
  done: new Set(["f2"]),
});

// ⚠ IN FILM ORDER WHATEVER ORDER THE MODEL WROTE THEM IN.
out.order = veoShots({
  veo: [
    { shot: 3, prompt: "c" },
    { shot: 1, prompt: "a" },
    { shot: 2, prompt: "b" },
  ],
  frames: timeline([2000, 2000, 2000]).frames,
}).shots.map((s) => s.shot);

// ------------------------------------------------------ 4. a take is not a shot
const withTakes = timeline([2000, 3000, 4000], [1, 3]);
const row = shotRow(withTakes.frames, withTakes.starts);
out.row = {
  before: withTakes.frames.length,
  after: row.frames.length,
  ids: row.frames.map((f) => f.id),
  // ⚠ `starts` IS FILTERED AT THE SAME INDICES, never recomputed.
  starts: row.starts,
  takes: withTakes.frames.filter(isTake).map((f) => f.id),
  // A film with no takes in it comes back as the SAME arrays, so a caller can
  // tell whether this did anything.
  identity: (() => {
    const plain = timeline([2000, 2000]);
    const same = shotRow(plain.frames, plain.starts);
    return same.frames === plain.frames && same.starts === plain.starts;
  })(),
};
// A picture that came off the board but is still a still is NOT a take, and a
// video the user dropped in (no `storyboard_id`) is not one either.
out.isTake = {
  panel: isTake({ kind: "image", src: { storyboard_id: "b1" } }),
  render: isTake({ kind: "video", src: { storyboard_id: "b1" } }),
  dropped: isTake({ kind: "video", src: { upload_id: "u1" } }),
  nothing: isTake(null),
};

// -------------------------------------------------------------- 5. is it due
out.due = {
  on: veoDue({ veo: true }, [{ shot: 1 }]),
  off: veoDue({ veo: false }, [{ shot: 1 }]),
  nothing: veoDue({ veo: true }, []),
};

process.stdout.write(JSON.stringify(out));
"""


def run_node():
    work = tempfile.mkdtemp(prefix="dir-chunk-")
    try:
        harness = os.path.join(work, "harness.mjs")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(HARNESS.replace("__VEO__", (AGENT / "veo_pass.js").as_uri()))
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


def pure_half():
    data = run_node()
    if data is None:
        print("  node is not on PATH, or veo_pass.js would not load — nothing checked.")
        return False

    print("\n⚠ THE CHUNKING — a submission is capped at MAX_VIDEO_BATCH, so a\n"
          "  48-shot film is four passes and the STOP lives between them\n")
    c = data["chunk"]
    check("⚠ 48 SHOTS AT A CAP OF 12 IS EXACTLY FOUR PASSES",
          c["12"]["passes"] == 4, json.dumps(c["12"]["sizes"]))
    check("...and not one of them is longer than the cap",
          all(n <= 12 for n in c["12"]["sizes"]), json.dumps(c["12"]["sizes"]))
    check("...none of them is empty",
          all(n > 0 for n in c["12"]["sizes"]), json.dumps(c["12"]["sizes"]))
    check("⚠ ...EVERY SHOT IS IN EXACTLY ONE PASS, in film order — a shot rendered\n"
          "       twice is billed twice, and one dropped is a hole in the film",
          c["12"]["flat"] == list(range(1, 49)), json.dumps(c["12"]["flat"][:6]))
    check("a cap of 6 is eight passes of six", c["6"]["sizes"] == [6] * 8,
          json.dumps(c["6"]["sizes"]))
    check("a cap of 1 is 48 passes of one", c["1"]["passes"] == 48, str(c["1"]["passes"]))
    check("a cap at or above the count is ONE pass",
          c["48"]["sizes"] == [48] and c["50"]["sizes"] == [48],
          json.dumps([c["48"]["sizes"], c["50"]["sizes"]]))
    check("nothing to render is no passes at all", c["empty"] == 0, str(c["empty"]))
    check("⚠ ...and a cap of 0 or junk is CLAMPED, not looped for ever — it comes\n"
          "       off a server config an operator can set",
          c["zero"] == 48 and c["junk"] == 48, json.dumps([c["zero"], c["junk"]]))

    print("\n⚠ THE LENGTH POLICY — the smallest take that COVERS the hold, never\n"
          "  the nearest: a take shorter than its shot ends on a drawing\n")
    check("Veo's menu is 4, 6 and 8 seconds", data["lengths"] == [4, 6, 8],
          json.dumps(data["lengths"]))
    cover = data["cover"]
    check("a 2.4s hold gets the 4-second take (and the shot grows to it)",
          cover["2400"] == 4, str(cover["2400"]))
    check("a 5.0s hold gets 6, not 4", cover["5000"] == 6, str(cover["5000"]))
    check("⚠ ...WHICH IS THE WHOLE POINT: rounding it DOWN would leave a second of\n"
          "       the drawing showing at the end of the shot",
          cover["5000"] != 4, str(cover["5000"]))
    check("an exact 4.0s hold gets 4", cover["4000"] == 4, str(cover["4000"]))
    check("⚠ ...AND SO DOES 4.06s — a hold is whole milliseconds out of a layout\n"
          "       pass, and paying half again for one nobody can see is absurd",
          cover["4060"] == 4, str(cover["4060"]))
    check("...but a hold past the slack does move up a size",
          cover["4061"] == 6, str(cover["4061"]))
    check("a 9.3s hold gets 8 — the longest there is — and keeps its own length",
          cover["9300"] == 8, str(cover["9300"]))
    check("an absurd hold still gets a real length rather than nothing",
          cover["60000"] == 8, str(cover["60000"]))
    check("a zero hold is the shortest take, not a zero-length request",
          cover["0"] == 4, str(cover["0"]))

    print("\n⚠ WHAT PHASE C REFUSES, AND SAYS OUT LOUD — every refusal the server\n"
          "  would make silently is made here first, with the reason on screen\n")
    s = data["shots"]
    kept = [row["shot"] for row in s["shots"]]
    check("the two renderable shots survive", kept == [1, 4], json.dumps(kept))
    check("...each carrying the length its own hold asked for",
          [row["seconds"] for row in s["shots"]] == [4, 4],
          json.dumps([(r["shot"], r["seconds"], r["hold_ms"]) for r in s["shots"]]))
    check("...and the clip id, not just the shot number — a number is a position\n"
          "       and the film moves",
          [row["frame_id"] for row in s["shots"]] == ["f1", "f4"],
          json.dumps([r["frame_id"] for r in s["shots"]]))
    why = " | ".join(row["why"] for row in s["skipped"])
    check("⚠ A SHOT ALREADY PAID FOR IS SKIPPED, and worded as money SAVED",
          any("already has a take you have paid for" in row["why"] for row in s["skipped"]),
          why)
    check("a PROMPTLESS shot is refused — Veo bills a blank one like any other",
          any("no motion prompt" in row["why"] for row in s["skipped"]), why)
    check("a shot that is not on the timeline is refused",
          any("no shot 9" in row["why"] for row in s["skipped"]), why)
    check("nothing renderable is lost or invented",
          len(s["shots"]) + len(s["skipped"]) == 5,
          json.dumps([len(s["shots"]), len(s["skipped"])]))
    check("⚠ ...AND THEY COME BACK IN FILM ORDER, whatever order the model wrote\n"
          "       them in — the passes are submitted in this order and watched",
          data["order"] == [1, 2, 3], json.dumps(data["order"]))

    print("\n⚠ A TAKE IS NOT A SHOT — `attachVeoClip` appends the render to `frames`,\n"
          "  so without this a second 🎬 run reads a 96-shot film that never existed\n")
    r = data["row"]
    check("three panels and two takes is a five-clip picture row",
          r["before"] == 5, str(r["before"]))
    check("⚠ ...AND A THREE-SHOT FILM", r["after"] == 3, str(r["after"]))
    check("...the panels, in order, and not the renders",
          r["ids"] == ["f1", "f2", "f3"], json.dumps(r["ids"]))
    check("⚠ ...WITH `starts` FILTERED AT THE SAME INDICES, never re-derived — the\n"
          "       editor's own layout knows about tracks and dragged clips",
          r["starts"] == [0, 2000, 5000], json.dumps(r["starts"]))
    check("a film with no takes comes back as the SAME arrays, by identity",
          r["identity"] is True)
    t = data["isTake"]
    check("a board panel is not a take", t["panel"] is False)
    check("a board render is", t["render"] is True)
    check("⚠ ...and a video the user DROPPED IN is not — it is a shot, and cutting\n"
          "       it out of the film would be the Director losing the user's clip",
          t["dropped"] is False)
    check("nothing is not a take", t["nothing"] is False)

    due = data["due"]
    check("with prompts and the box ticked, the pass is due", due["on"]["due"] is True)
    check("un-ticked, it is not — and says so in words the panel prints",
          due["off"]["due"] is False and "switched off" in due["off"]["why"],
          json.dumps(due["off"]))
    check("with no motion prompts it is not due either — which is every run of the\n"
          "       rules planner, because arithmetic cannot write one",
          due["nothing"]["due"] is False and "no motion prompts" in due["nothing"]["why"],
          json.dumps(due["nothing"]))
    return True


# ===========================================================================
# THE MONEY HALF — the quote, and the mixed batch reaching the server
# ===========================================================================
def money_half():
    from fastapi.testclient import TestClient

    from server import config, worker
    from server.director import _quote_veo_run
    from server.jobs import get_store
    from server.main import app
    from server.schemas import AnimaticDirectorShot, RenderSettings

    # ⚠ WITHOUT THIS THE SUITE WOULD SUBMIT REAL VEO WORK AND BILL FOR IT.
    submitted: list[tuple[str, list[str], dict]] = []
    worker.submit_animatic_animate = lambda job_id, clip_ids, render: submitted.append(
        (job_id, list(clip_ids), dict(render))
    )

    client = TestClient(app)
    store = get_store()
    email = f"_chunk_{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "chunk-pass-12345"})
    assert r.status_code == 201, r.text
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}

    render = RenderSettings()
    cap = config.MAX_VIDEO_BATCH

    print(f"\n⚠ THE MONEY — the total is the SUM OF THE PASSES, to the penny\n"
          f"  (batch cap {cap}, {render.tier}/{render.resolution}, "
          f"audio {'on' if render.generate_audio else 'off'})\n")

    # 48 shots at a deliberate MIXTURE of lengths — the case where rounding the
    # whole list once and rounding four twelfths of it and adding disagree.
    shots = [
        AnimaticDirectorShot(
            shot=i + 1, frame_id=f"f{i + 1}", label=f"Shot {i + 1}",
            prompt="move", seconds=[4, 6, 8][i % 3], hold_ms=2400,
        )
        for i in range(48)
    ]
    quote = _quote_veo_run(shots, render)
    check("⚠ 48 SHOTS IS EXACTLY FOUR PASSES", len(quote.passes) == 4,
          str(len(quote.passes)))
    check("...none longer than the cap",
          all(p.shots <= cap for p in quote.passes),
          json.dumps([p.shots for p in quote.passes]))
    check("the quote reports the cap it used", quote.batch == cap, str(quote.batch))
    check("⚠ THE TOTAL IS THE SUM OF THE PASSES, EXACTLY",
          quote.total.usd == round(sum(p.usd for p in quote.passes), 2),
          f"total {quote.total.usd} vs sum {round(sum(p.usd for p in quote.passes), 2)}")
    check("...and so is the shot count",
          quote.total.shots == sum(p.shots for p in quote.passes) == 48,
          str(quote.total.shots))
    check("...and the seconds", quote.total.seconds == sum(p.seconds for p in quote.passes),
          str(quote.total.seconds))
    check("⚠ ...AND THE SECONDS ARE THE SHOTS' OWN LENGTHS, not the settings' —\n"
          "       48 shots of 4/6/8 is 288s, and 48 × 8 would be 384",
          quote.total.seconds == sum(s.seconds for s in shots) == 288,
          str(quote.total.seconds))
    check("the film is priced above nothing", quote.total.usd > 0, str(quote.total.usd))
    check("nothing to render is no passes and no charge",
          _quote_veo_run([], render).total.usd == 0
          and not _quote_veo_run([], render).passes)

    # ⚠ THE ROUNDING CASE, ON PURPOSE. A per-shot price with a third decimal is
    # exactly where "quote the whole list" and "quote the passes and add" part
    # company, and this is the identity the user reads off the screen.
    odd = [
        AnimaticDirectorShot(shot=i + 1, frame_id=f"g{i + 1}", prompt="m", seconds=4)
        for i in range(cap * 2 + 3)
    ]
    oddq = _quote_veo_run(odd, render)
    check("⚠ ...AND IT HOLDS ON A RAGGED RUN TOO — the last pass is a short one,\n"
          "       which is where a second calculation would drift by a penny",
          oddq.total.usd == round(sum(p.usd for p in oddq.passes), 2),
          f"total {oddq.total.usd} vs sum {round(sum(p.usd for p in oddq.passes), 2)}")
    check("...the last pass is the remainder", oddq.passes[-1].shots == 3,
          str(oddq.passes[-1].shots))

    print("\n⚠ THE MIXED BATCH REACHES THE SERVER — one submission, a 4-second take\n"
          "  and an 8-second one, priced and recorded as themselves\n")
    frames = [
        {
            "id": f"fr{i}",
            "src": {"kind": "upload", "upload_id": uuid.uuid4().hex[:12]},
            "duration_ms": 2000,
            "label": f"Shot {i + 1}",
        }
        for i in range(3)
    ]
    res = client.post("/animatics", headers=auth,
                      json={"title": "Chunk check", "frames": frames})
    assert res.status_code == 201, res.text
    job_id = res.json()["job_id"]
    ids = [f["id"] for f in frames]

    body = {
        "frame_ids": ids,
        "prompts": {fid: "he turns to camera" for fid in ids},
        "durations": {ids[0]: 4, ids[1]: 8, ids[2]: 6},
        "render": render.model_dump(),
    }
    r = client.post(f"/animatics/{job_id}/animate/estimate", headers=auth, json=body)
    check("the estimate answers", r.status_code == 200, r.text[:200])
    est = r.json()
    check("⚠ IT PRICES THE MIXTURE (4+8+6 = 18s), NOT `3 × duration_seconds`",
          est["seconds"] == 18, str(est["seconds"]))
    check("...which is not what the settings alone would have said",
          est["seconds"] != 3 * render.duration_seconds, str(est["seconds"]))
    check("and it costs what those seconds cost",
          est["usd"] == _quote_veo_run(
              [AnimaticDirectorShot(seconds=n) for n in (4, 8, 6)], render
          ).total.usd,
          str(est["usd"]))
    check("an estimate submits nothing", submitted == [], json.dumps(submitted))

    r = client.post(f"/animatics/{job_id}/animate", headers=auth, json=body)
    check("the render is accepted", r.status_code == 202, r.text[:200])
    check("⚠ ...AND THE ESTIMATE'S NUMBER IS THE ONE IN THE MESSAGE — the two\n"
          "       endpoints take the same body so they cannot say different things",
          f"${est['usd']:.2f}" in r.json()["message"], r.json()["message"])
    clips = (store.get(job_id).result or {}).get("veo_clips") or []
    check("three records were written", len(clips) == 3, str(len(clips)))
    by_frame = {c["frame_id"]: c for c in clips}
    check("⚠ EACH RECORD CARRIES ITS OWN LENGTH — without this the worker renders\n"
          "       the whole batch at the settings' length and the quote is a lie",
          [by_frame[ids[i]]["seconds"] for i in range(3)] == [4, 8, 6],
          json.dumps([by_frame[i]["seconds"] for i in ids]))
    check("records live in RESULT, never params",
          "veo_clips" in (store.get(job_id).result or {})
          and "veo_clips" not in (store.get(job_id).params or {}))

    # ⚠ AND A LENGTH VEO WILL NOT RENDER IS NOT SENT ON. The menu is 4/6/8; a 5
    # would be a PAID refusal, so it falls back to the settings' own length.
    from server.jobs import get_store as _gs
    from server.schemas import JobStatus

    _gs().update(job_id, status=JobStatus.QUEUED)
    body2 = {**body, "durations": {ids[0]: 5, ids[1]: 0, ids[2]: 999}, "force": True}
    r = client.post(f"/animatics/{job_id}/animate/estimate", headers=auth, json=body2)
    check("⚠ A LENGTH OFF THE MENU FALLS BACK TO THE SETTINGS, never goes to Veo",
          r.json()["seconds"] == 3 * render.duration_seconds, str(r.json()["seconds"]))

    client.delete(f"/animatics/{job_id}", headers=auth)


def main():
    ran = pure_half()
    money_half()
    print()
    if failures:
        print(f"{len(failures)} FAILED:")
        for name in failures:
            print("  -", name)
        return 1
    if not ran:
        print("The money half passed; the pure half needs node.")
        return 2
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
