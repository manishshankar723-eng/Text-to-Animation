// AdminShowcase.jsx — the picture-and-video wall on the PUBLIC Explore page.
//
// ⚠ WHAT THIS EXISTS FOR. Explore is the marketing page a STRANGER lands on
// now, and its wall used to be the signed-in account's own projects — which is
// an empty grid to somebody who has not signed up yet, on the one screen whose
// whole job is to make them want to. So the wall is curated: an administrator
// uploads finished work here, item by item, and that is what a visitor sees.
//
// ⚠ NO CUSTOMER'S WORK IS EVER ON IT. Nothing is shared by default in this app
// and there is no public gallery; every item on this screen was put there by
// hand. See the header of `server/showcase.py`.
//
// ⚠ AN ITEM IS AN IMAGE OR A VIDEO, and the video is the point — asked for
// directly: *"the videos or images should be clickable and be able to use it
// properly play"*. One upload button takes either, and the server reads the
// file's own type to decide which it got.
//
// ⚠ A VIDEO WANTS A POSTER AND THE SERVER NOW TAKES ONE ITSELF. This used to
// say a poster was a SECOND upload, because "there is no ffprobe on this
// install" — which was true and beside the point: ffmpeg extracts frames, not
// ffprobe, and the exporter has been shipping ffmpeg all along. Uploading a clip
// grabs a still on the way in (`showcase.poster_from_video`). Reported as a bug,
// and it was one: *"when i upload video from admin panel but when i see explore
// page so no thumbnail show in my upload video."*
//
// ⚠ SO "ADD STILL" IS THE OVERRIDE NOW, NOT THE ONLY WAY IN, and it still
// matters: the frame that sells a film is rarely the one it opens on. What a
// person picks here is never overwritten by a later grab.
//
// ⚠ AND THE ROW STILL SAYS WHEN THERE IS NO STILL, because there is one case
// left — a clip whose every probe came back black is refused rather than shipped
// as a black rectangle. That card really does draw a glyph, and somebody has to
// be told why rather than left wondering.
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api.js";
// ⚠ RULEBOOK E1: a box somebody must EDIT grows to its text. The caption is the
// only multi-line field here and it is the one that gets rewritten most.
import GrowTextarea from "../components/GrowTextarea.jsx";
// The workflow tag. ⚠ THE BUILT-IN LIST, ON PURPOSE — the same reasoning
// AdminBanners keeps for its target picker: these ids are what `App.jsx` can
// actually navigate to after a visitor signs in, which is where the viewer's
// "Use this workflow" button sends them.
import { WORKFLOWS } from "../components/Sidebar.jsx";

// The shapes a card may be laid out at, in words. ⚠ THE VALUES MUST MATCH
// `showcase.ASPECTS` on the server — it refuses anything else.
const ASPECT_LABEL = {
  "16:9": "Wide (16:9)",
  "4:5": "Portrait (4:5)",
  "1:1": "Square (1:1)",
  "9:16": "Tall / Reel (9:16)",
};

// Bytes → the sentence a person reads. Same idea as `formatBytes` in
// LibraryList, kept local because this screen only ever prints two ceilings.
function mb(bytes) {
  return `${Math.round((bytes || 0) / (1024 * 1024))}MB`;
}

function blankForm() {
  return {
    title: "",
    blurb: "",
    workflow: "",
    aspect: "16:9",
  };
}

export default function AdminShowcase() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(blankForm);
  const [formError, setFormError] = useState("");
  // ⚠ HAS ANYBODY TYPED YET. Without this the form opens with "needs a title"
  // already printed in red under an untouched box, which reads as an accusation
  // on a screen somebody came to CREATE something on. Same as AdminBanners.
  const [touched, setTouched] = useState(false);
  // Which row is asking "are you sure?". ⚠ DELETING THROWS THE VIDEO AWAY, so
  // it asks — hiding does not, because hiding is reversible in one click.
  const [confirming, setConfirming] = useState("");

  // ⚠ TWO HIDDEN INPUTS, NOT ONE. The media picker accepts video as well as
  // images and the poster picker never does; a single input would have to
  // rewrite its own `accept` between clicks, and the browser caches that
  // decision often enough to be a bug nobody can reproduce.
  const mediaRef = useRef(null);
  const posterRef = useRef(null);
  // Which row an upload is FOR — one input shared by every row, one `onChange`.
  const uploadFor = useRef("");

  const load = useCallback(() => {
    setError("");
    api
      .adminListShowcase()
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(load, [load]);

  /** Every write funnels through here: one place that reloads and reports. */
  async function run(what, call, { silent = false } = {}) {
    setBusy(what);
    setError("");
    try {
      await call();
      load();
      return "";
    } catch (e) {
      if (!silent) setError(e.message);
      return e.message || "That didn't work.";
    } finally {
      setBusy("");
    }
  }

  function update(patch) {
    setForm((f) => ({ ...f, ...patch }));
    setTouched(true);
    setFormError("");
  }

  if (!data) {
    return (
      <div className="admin-body">
        <div className="card admin-card">
          <p className="muted">
            {error ? <span className="error">{error}</span> : "Loading…"}
          </p>
        </div>
      </div>
    );
  }

  const rows = data.items || [];
  const limits = data.limits || {};
  const shownCount = rows.filter((r) => r.live).length;
  const problem = !form.title.trim() ? "A showcase item needs a title." : "";

  /**
   * Move an item up or down the wall.
   *
   * ⚠ RULEBOOK E6: a list whose ORDER is its meaning needs ↑ / ↓, not just add
   * and delete. The first item is the first thing a visitor's eye lands on
   * under the banners, so "third" is a decision somebody has to be able to make.
   *
   * ⚠ AND IT SWAPS RANKS RATHER THAN RENUMBERING THE LIST — two writes, not
   * twenty, and a row nobody moved keeps the number it had. Straight out of
   * `AdminBanners.move`, which took it from `moveShot()` on the storyboard.
   */
  function move(row, delta) {
    const at = rows.findIndex((r) => r.id === row.id);
    const to = at + delta;
    if (at < 0 || to < 0 || to >= rows.length) return;
    const other = rows[to];
    run(row.id, async () => {
      await api.adminUpdateShowcase(row.id, { rank: other.rank });
      await api.adminUpdateShowcase(other.id, { rank: row.rank });
    });
  }

  const acceptMedia = [
    ...(data.allowed_image_types || []),
    ...(data.allowed_video_types || []),
  ].join(",");

  return (
    <div className="admin-body">
      {error && <p className="error">{error}</p>}

      <div className="info-msg admin-note-box">
        This is the wall of work under the banners on <strong>Explore</strong> —
        the page anybody who is not signed in lands on. Every item here is
        something <strong>you</strong> uploaded; no customer's project is ever
        shown. A visitor can click any card and watch it, and the viewer offers
        them the workflow it was made with — which asks them to sign in.
      </div>

      <section className="card admin-card">
        <div className="admin-section-head">
          <div>
            <h2 className="admin-h2">Explore showcase</h2>
            <p className="muted tiny admin-group-blurb">
              Pictures are scaled to {data.image_max_px}px (PNG, JPEG or WEBP,
              up to {mb(data.max_image_bytes)}). Videos are stored as they
              arrive — <strong>MP4 or WEBM only</strong>, up to{" "}
              {mb(data.max_video_bytes)}. The page shows the first{" "}
              {data.max_public}; {shownCount} of {rows.length}{" "}
              {shownCount === 1 ? "is" : "are"} showing now.
            </p>
          </div>
          {/* Not a ghost when it IS the action — same reasoning as the Banners
              and Offers sections. Cancel stays a ghost. */}
          <button
            className={`btn small ${open ? "ghost" : ""}`}
            onClick={() => {
              setFormError("");
              setOpen((o) => !o);
            }}
          >
            {open ? "Cancel" : "＋ New item"}
          </button>
        </div>

        {open && (
          <div className="admin-rollout">
            <label className="admin-rollout-row wide">
              <span className="muted tiny">Title</span>
              <input
                className="admin-search"
                maxLength={limits.title}
                value={form.title}
                placeholder="Chai break — 20s spot"
                onChange={(e) => update({ title: e.target.value })}
              />
            </label>
            <label className="admin-rollout-row wide">
              <span className="muted tiny">
                The line under it — two lines is what fits
              </span>
              <GrowTextarea
                className="admin-search admin-banner-body"
                rows={2}
                maxLength={limits.blurb}
                value={form.blurb}
                placeholder="Script to finished cut in one afternoon."
                onChange={(e) => update({ blurb: e.target.value })}
              />
            </label>
            <label className="admin-rollout-row">
              <span className="muted tiny">Made with (optional)</span>
              <select
                className="admin-search"
                value={form.workflow}
                onChange={(e) => update({ workflow: e.target.value })}
              >
                <option value="">No tag</option>
                {WORKFLOWS.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="admin-rollout-row">
              <span className="muted tiny">Shape</span>
              <select
                className="admin-search"
                value={form.aspect}
                onChange={(e) => update({ aspect: e.target.value })}
              >
                {(data.aspects || []).map((a) => (
                  <option key={a} value={a}>
                    {ASPECT_LABEL[a] || a}
                  </option>
                ))}
              </select>
            </label>

            {/* ⚠ NOT AN `admin-rollout-row`. That class is the form GRID's cell
                and lays its children out in a column, so a sentence with a
                <strong> in it came out as three stacked lines. */}
            <p className="muted tiny admin-banner-hint">
              The picture or video is added after this — every item gets its own{" "}
              <strong>Add file</strong> button in the list below.{" "}
              <strong>Shape</strong> only matters for a video: a picture's real
              shape is measured when it is uploaded and wins over this.
            </p>

            {((touched && problem) || formError) && (
              <p className="error admin-offer-error">{problem || formError}</p>
            )}
            <div className="admin-actions">
              <button
                className="btn primary"
                disabled={busy === "new" || Boolean(problem)}
                onClick={async () => {
                  setFormError("");
                  const failed = await run(
                    "new",
                    () =>
                      api.adminCreateShowcase({
                        title: form.title,
                        blurb: form.blurb,
                        workflow: form.workflow,
                        aspect: form.aspect,
                        // Newest item goes to the BACK of the wall, which is
                        // where somebody adding one expects it. The arrows move
                        // it from there.
                        rank: rows.length,
                      }),
                    { silent: true }
                  );
                  // ⚠ ONLY CLOSES WHEN IT WORKED. Closing regardless loses every
                  // field that was filled in, so the person retypes the whole
                  // item just to find out what was wrong with it.
                  if (failed) setFormError(failed);
                  else {
                    setForm(blankForm());
                    setTouched(false);
                    setOpen(false);
                  }
                }}
              >
                {busy === "new" ? "Creating…" : "Create"}
              </button>
            </div>
          </div>
        )}

        {/* The two hidden inputs — see `mediaRef` / `posterRef` above. */}
        <input
          ref={mediaRef}
          type="file"
          accept={acceptMedia}
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            const id = uploadFor.current;
            e.target.value = "";
            if (file && id) run(id, () => api.adminUploadShowcaseMedia(id, file));
          }}
        />
        <input
          ref={posterRef}
          type="file"
          accept={(data.allowed_image_types || []).join(",")}
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            const id = uploadFor.current;
            e.target.value = "";
            if (file && id) run(id, () => api.adminUploadShowcasePoster(id, file));
          }}
        />

        {rows.length === 0 ? (
          <p className="muted tiny">
            Nothing here yet — Explore draws its banners and workflow tiles and
            no wall at all. Add the work you want a visitor to see first.
          </p>
        ) : (
          <ul className="admin-banner-list">
            {rows.map((it, i) => (
              <li className="admin-banner-row" key={it.id}>
                <span className="admin-banner-pic">
                  {/* ⚠ THE POSTER, NOT THE CLIP. A `<video>` per row would have
                      the panel downloading every uploaded film the moment this
                      screen opens. The badge says which kind it is. */}
                  {it.poster_url || (it.kind === "image" && it.media_url) ? (
                    <img
                      src={api.absoluteUrl(it.poster_url || it.media_url)}
                      alt=""
                    />
                  ) : (
                    <span className="muted tiny">
                      {it.has_media ? "No still" : "No file"}
                    </span>
                  )}
                </span>

                <span className="admin-banner-text">
                  <span className="admin-banner-kicker">
                    {it.kind === "video"
                      ? "▶ Video"
                      : it.kind === "image"
                        ? "Picture"
                        : "—"}
                    {it.aspect ? ` · ${it.aspect}` : ""}
                  </span>
                  <span className="admin-banner-title">{it.title}</span>
                  <span className="muted tiny">{it.blurb || "No line"}</span>
                  <span className="muted tiny">
                    {it.workflow
                      ? `Made with: ${
                          WORKFLOWS.find((w) => w.id === it.workflow)?.label ||
                          it.workflow
                        }`
                      : "No workflow tag"}
                  </span>
                  {/* ⚠ THE ONE THING THIS SCREEN MUST SAY OUT LOUD. An item can
                      be switched ON and still not be on the page, because
                      nothing has been uploaded to it — and hunting a website
                      for a card that was never going to be there is a bad
                      half-hour. */}
                  {it.active && !it.has_media && (
                    <span className="muted tiny">
                      Not on the page yet — it needs a file.
                    </span>
                  )}
                  {/* ⚠ THIS NOW MEANS THE GRAB FAILED, not "nobody uploaded
                      one" — the server takes a still on upload. The commonest
                      reason left is a clip that is black wherever it looked. */}
                  {it.kind === "video" && it.has_media && !it.has_poster && (
                    <span
                      className="muted tiny"
                      title="A still is taken from the clip automatically. This one came back black wherever it looked, so the card draws a glyph instead. Add one by hand to fix it."
                    >
                      Couldn't take a still — add one to fix the card.
                    </span>
                  )}
                </span>

                {/* ⚠ ORDER IS THE MEANING IN THIS LIST — RULEBOOK E6. Disabled
                    at either end, and `move` no-ops there too. */}
                <span className="admin-banner-order">
                  <button
                    className="btn ghost small"
                    title="Move up"
                    disabled={i === 0 || busy === it.id}
                    onClick={() => move(it, -1)}
                  >
                    ↑
                  </button>
                  <button
                    className="btn ghost small"
                    title="Move down"
                    disabled={i === rows.length - 1 || busy === it.id}
                    onClick={() => move(it, 1)}
                  >
                    ↓
                  </button>
                </span>

                <span className="admin-banner-acts">
                  <span className={`badge ${it.live ? "ok" : ""}`}>
                    {it.live ? "Showing" : it.active ? "Needs a file" : "Hidden"}
                  </span>
                  <button
                    className="btn ghost small"
                    disabled={busy === it.id}
                    /* ⚠ THE TOOLTIP CARRIES THE DISTINCTION THIS ROW KEEPS
                       GETTING WRONG. Two buttons sit next to each other and
                       only one of them changes what a visitor SEES when they
                       click a card. Asked, with a screenshot: *"maine admin
                       panel se image daala hai — magar explore pe khol raha hun
                       to video khul raha hai, image nahi"*. The image had gone
                       in as the STILL; the item was still a video. Nothing on
                       the row said the two buttons meant different things. */
                    title="THE THING ITSELF — what opens when somebody clicks this card. A picture makes this an IMAGE card; an MP4 or WEBM makes it a VIDEO card."
                    onClick={() => {
                      uploadFor.current = it.id;
                      mediaRef.current?.click();
                    }}
                  >
                    {busy === it.id
                      ? "Working…"
                      : it.has_media
                        ? "Replace file"
                        : "📁 Add file"}
                  </button>
                  {/* A still is only meaningful on a clip — a picture IS its
                      own poster, and the server clears one when a video is
                      replaced by an image. */}
                  {it.kind === "video" && (
                    <button
                      className="btn ghost small"
                      disabled={busy === it.id}
                      title="ONLY THE COVER — the frame shown before anybody presses play. It does NOT change what opens: this card stays a video. One is taken from the clip automatically; this overrules it and is never overwritten."
                      onClick={() => {
                        uploadFor.current = it.id;
                        posterRef.current?.click();
                      }}
                    >
                      {it.has_poster ? "Replace still" : "🖼 Add still"}
                    </button>
                  )}
                  {it.has_poster && (
                    <button
                      className="btn ghost small"
                      disabled={busy === it.id}
                      onClick={() =>
                        run(it.id, () => api.adminRemoveShowcasePoster(it.id))
                      }
                    >
                      Remove still
                    </button>
                  )}
                  <button
                    className="btn ghost small"
                    disabled={busy === it.id}
                    onClick={() =>
                      run(it.id, () =>
                        api.adminUpdateShowcase(it.id, { active: !it.active })
                      )
                    }
                  >
                    {it.active ? "Hide" : "Show"}
                  </button>
                  {/* ⚠ DELETE ASKS FIRST AND HIDE DOES NOT, because only one of
                      them is reversible. Deleting takes the file with it. */}
                  {confirming === it.id ? (
                    <>
                      <button
                        className="btn small"
                        disabled={busy === it.id}
                        onClick={() => {
                          setConfirming("");
                          run(it.id, () => api.adminDeleteShowcase(it.id));
                        }}
                      >
                        Delete for good
                      </button>
                      <button
                        className="btn ghost small"
                        onClick={() => setConfirming("")}
                      >
                        Keep
                      </button>
                    </>
                  ) : (
                    <button
                      className="btn ghost small"
                      disabled={busy === it.id}
                      onClick={() => setConfirming(it.id)}
                    >
                      Delete
                    </button>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
