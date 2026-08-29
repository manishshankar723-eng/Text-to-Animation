// AdminLanding.jsx — the four pictures in the LANDING page hero, one per workflow.
//
// ⚠ WHAT THIS REPLACES. The hero's four tiles were HAND-DRAWN SVG inside
// `Landing.jsx` — a screenplay page, four board panels, a pose strip and a
// timeline — so the biggest picture on the page a stranger lands on could only be
// changed by a developer, and could never be a real frame from the product.
// Asked for directly: *"mai chahta hun ki ye four icon ke jagh mai image lagun so
// admin panel mai landing page ka fuction bano so mai image dall sakun har
// workflow ka."*
//
// ⚠ THE LIST IS THE WORKFLOW CATALOGUE, NOT A LIST YOU ADD TO. There is no
// "＋ New" button here and no Delete, which is the one real difference from
// AdminBanners: a banner is a row somebody invents, a hero tile belongs to a
// WORKFLOW. That is what the last half of the request asked for — *"aage ami aur
// v workflow banau to o v same fuctiuon mai chale"* — and it is why a seventh
// workflow needs no code: it appears in this list the moment it is in the
// catalogue, with its own empty picture slot waiting.
//
// ⚠ VISIBILITY IS NOT EDITED HERE, IT IS ONLY REPORTED. Which workflows a
// stranger sees is the Features tab's job (live / soon / hidden), and putting a
// second switch for it on this screen would be two places to disagree about what
// is on the front page. So every row says what will actually happen to its
// picture instead — *"jo live hai uska dikhe image yaha pe aur jo hide hai uska
// nhi dikhe"* — because an operator who uploads a picture to a hidden workflow
// would otherwise see a perfect thumbnail here and no tile on the page, with
// nothing explaining why.
//
// ⚠ AN EMPTY LIST OF PICTURES IS THE SHIPPED STATE, NOT A BROKEN ONE. With no
// picture on a workflow the hero draws exactly what it drew before this screen
// existed. The panel adds pictures; it is not a prerequisite for the page.
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api.js";
// The same glyph the rail, the landing cards and the hero strip draw, so a row
// here is recognisable as the workflow it belongs to before the label is read.
import WorkflowIcon from "../components/WorkflowIcon.jsx";

// What the Features tab calls each state, in the words this screen needs — which
// are about the PICTURE, not about the workflow. "Hidden" is the only one that
// takes a tile off the page.
const STATUS_NOTE = {
  live: "Live",
  soon: "Soon",
  hidden: "Hidden",
};

export default function AdminLanding() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const fileRef = useRef(null);
  // Which row an upload is FOR. One hidden file input shared by every row —
  // one input, one `onChange`, exactly as AdminBanners does it.
  const uploadFor = useRef("");

  const load = useCallback(() => {
    setError("");
    api
      .adminListLandingArt()
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  useEffect(load, [load]);

  /** Every write funnels through here: one place that reloads and reports. */
  async function run(what, call) {
    setBusy(what);
    setError("");
    try {
      await call();
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
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

  const rows = data.workflows || [];
  const tiles = data.hero_tiles || 4;
  const drawn = rows.filter((w) => w.in_hero);
  const withPicture = drawn.filter((w) => w.has_image).length;

  return (
    <div className="admin-body">
      {error && <p className="error">{error}</p>}

      <div className="info-msg admin-note-box">
        The <strong>first {tiles}</strong> workflows a visitor is allowed to see
        get a tile in the big picture at the top of the landing page. Put a
        picture on a workflow and its tile becomes that picture; leave it empty
        and the page keeps its own drawing. Hiding a workflow in{" "}
        <strong>Features</strong> takes its tile off the page and brings it
        straight back when you un-hide it — the picture is kept either way.
      </div>

      <section className="card admin-card">
        <div className="admin-section-head">
          <div>
            <h2 className="admin-h2">Landing page pictures</h2>
            <p className="muted tiny admin-group-blurb">
              {withPicture} of the {drawn.length} tile
              {drawn.length === 1 ? "" : "s"} on the page{" "}
              {withPicture === 1 ? "has" : "have"} a picture. Pictures are scaled
              to {data.image_max_px}px and may be PNG, JPEG or WEBP. A tile is{" "}
              <strong>4 × 3</strong> and crops from the centre, so anything
              square or wider works — a tall photograph will lose its top and
              bottom.
            </p>
          </div>
        </div>

        {/* One hidden input for every row — see `uploadFor`. */}
        <input
          ref={fileRef}
          type="file"
          accept={(data.allowed_types || []).join(",")}
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            const id = uploadFor.current;
            e.target.value = "";
            if (file && id) run(id, () => api.adminUploadLandingImage(id, file));
          }}
        />

        <ul className="admin-art-list">
          {rows.map((w) => (
            <li className="admin-art-row" key={w.id}>
              {/* ⚠ 4 × 3 AND THE REAL PICTURE, at the shape the hero draws it.
                  A thumbnail in a different aspect ratio to the tile is a
                  thumbnail that lies about the crop. */}
              <span className="admin-art-pic">
                {w.image_url ? (
                  <img src={api.absoluteUrl(w.image_url)} alt="" />
                ) : (
                  <span className="admin-art-glyph" aria-hidden="true">
                    {/* ⚠ THE EMOJI FALLBACK IS NOT OPTIONAL. `WorkflowIcon`
                        draws NOTHING for an id it has no glyph for, so a
                        workflow added after this build would leave an empty
                        square here — and this list is the one screen that has to
                        keep working for exactly that workflow. */}
                    <WorkflowIcon id={w.id} fallback={w.icon} />
                  </span>
                )}
              </span>

              <span className="admin-art-text">
                <span className="admin-art-title">{w.label}</span>
                {/* ⚠ THE ONE SENTENCE THIS SCREEN EXISTS TO SAY. Three
                    different reasons a picture is not on the page, and the
                    operator cannot see any of them from the thumbnail. */}
                <span className="muted tiny">
                  {!w.on_page
                    ? "Hidden in Features — no tile on the page. The picture is kept."
                    : !w.in_hero
                      ? `Not in the first ${tiles} — no tile. Move it up in Features to draw it.`
                      : w.has_image
                        ? "Showing on the landing page."
                        : "On the page, drawn — upload a picture to replace the drawing."}
                </span>
                <span className="muted tiny">
                  {w.has_image && w.updated_at
                    ? `Picture changed ${new Date(w.updated_at).toLocaleDateString()}${
                        w.updated_by ? ` by ${w.updated_by}` : ""
                      }`
                    : "No picture yet"}
                </span>
              </span>

              <span className="admin-art-acts">
                {/* Green only when the picture is genuinely on the page — the
                    same rule the banner rows follow with `active`. */}
                <span
                  className={`badge ${
                    !w.on_page ? "" : w.in_hero && w.has_image ? "ok" : "queued"
                  }`}
                >
                  {w.on_page && w.in_hero && w.has_image
                    ? "Showing"
                    : STATUS_NOTE[w.status] || w.status}
                </span>
                <button
                  className="btn ghost small"
                  disabled={busy === w.id}
                  onClick={() => {
                    uploadFor.current = w.id;
                    fileRef.current?.click();
                  }}
                >
                  {busy === w.id
                    ? "Working…"
                    : w.has_image
                      ? "Replace picture"
                      : "📁 Add picture"}
                </button>
                {/* ⚠ NO CONFIRM STEP, AND IT DOES NOT NEED ONE. Removing a
                    picture loses only the picture and is undone by uploading it
                    again — there is no row to delete and nothing else attached
                    to it, which is what the banner rows have to ask about. */}
                {w.has_image && (
                  <button
                    className="btn ghost small"
                    disabled={busy === w.id}
                    onClick={() =>
                      run(w.id, () => api.adminRemoveLandingImage(w.id))
                    }
                  >
                    Remove picture
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>

        {rows.length === 0 && (
          <p className="muted tiny">
            No workflows in the catalogue — the landing page is drawing its
            built-in tiles.
          </p>
        )}
      </section>
    </div>
  );
}
