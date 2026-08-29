// AdminBanners.jsx — the two billboards on Explore: their words, their
// pictures, their order and whether they are showing at all.
//
// ⚠ WHAT THIS REPLACES. Explore's banners were BUILT FROM THE WORKFLOW LIST —
// the headline was a workflow's name, the body was the first line of its pitch
// from the landing page, and the artwork was that workflow's own glyph, faded.
// So the one part of the app whose whole job is to say something to a customer
// could only be changed by a developer, and could not carry a picture at all.
// Asked for directly: *"this banner should be change aur hide by the admin — of
// it text and image."*
//
// ⚠ AN EMPTY LIST IS THE SHIPPED STATE, NOT A BROKEN ONE. With no live banner in
// a slot, Explore draws exactly what it drew before this screen existed. That is
// why the empty state here says so out loud rather than reading as "you have
// nothing" — the panel adds a voice, it is not a prerequisite for the page.
//
// ⚠ TWO SLOTS, ONE LIST. `hero` is the rotating billboard on the left (up to
// four, which is the dot count); `side` is the fixed one on the right. They are
// the same object in two places on one page, so they are edited together and
// only grouped for reading — the opposite of the sale/coupon split in
// AdminSales, where the two are edited at completely different moments.
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api.js";
// ⚠ RULEBOOK E1: a box somebody must EDIT grows to its text. The body is the
// only multi-line field here and it is the one that gets rewritten most.
import GrowTextarea from "../components/GrowTextarea.jsx";
// The button's destinations. ⚠ THE BUILT-IN LIST, ON PURPOSE: this is a picker
// of internal addresses, and the six ids are what `App.jsx` can actually
// navigate to. An administrator who adds a seventh workflow can still point a
// banner at it — the "A web address" box takes anything, and the server accepts
// a bare workflow id too (see `_TARGET_RE` in banners.py).
import { WORKFLOWS } from "../components/Sidebar.jsx";

const SLOT_LABEL = {
  hero: "Rotating (left)",
  side: "Fixed (right)",
};

// The "where does the button go" picker's two non-workflow options.
const TARGET_NONE = "";
const TARGET_LINK = "__link__";

function blankForm() {
  return {
    slot: "hero",
    kicker: "",
    title: "",
    body: "",
    cta_label: "",
    // What the dropdown is on. Kept apart from `link` so switching to "a web
    // address" and back does not lose what was typed in either.
    target: TARGET_NONE,
    link: "",
  };
}

/** The form's two target fields → the one string the server stores. */
function targetOf(form) {
  if (form.target === TARGET_LINK) return form.link.trim();
  return form.target;
}

export default function AdminBanners() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(blankForm);
  const [formError, setFormError] = useState("");
  // ⚠ HAS ANYBODY TYPED YET. Without this the form opened with "A banner needs
  // a heading" already printed in red under an untouched box — which reads as
  // an accusation rather than as help, and is the first thing anyone sees on a
  // screen they came to CREATE something on. The disabled Create button is the
  // signal until then.
  const [touched, setTouched] = useState(false);
  // Which row is asking "are you sure?". ⚠ DELETING THROWS AWAY THE PICTURE
  // TOO, so it asks — hiding does not, because hiding is reversible in a click.
  const [confirming, setConfirming] = useState("");
  const fileRef = useRef(null);
  // Which row an upload is FOR. The file input is a single hidden element
  // shared by every row — one input, one `onChange`, rather than one per row.
  const uploadFor = useRef("");

  const load = useCallback(() => {
    setError("");
    api
      .adminListBanners()
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

  const rows = data.banners || [];
  const limits = data.limits || {};
  const problem = !form.title.trim()
    ? "A banner needs a heading."
    : form.target === TARGET_LINK && !form.link.trim()
      ? "Put in the address the button should open."
      : "";

  /**
   * Move a banner up or down IN ITS OWN SLOT.
   *
   * ⚠ RULEBOOK E6: a list whose ORDER is its meaning needs ↑ / ↓, not just add
   * and delete. The rotating billboard shows its cards in this order and the
   * first one is what a customer sees on landing, so "third" is a decision
   * somebody has to be able to make.
   *
   * ⚠ AND IT SWAPS RANKS RATHER THAN RENUMBERING THE LIST — two writes, not
   * five, and a row nobody moved keeps the number it had. The handler no-ops at
   * either end so a keypress that beats the re-render cannot wrap the first card
   * to the bottom, exactly as `moveShot()` does on the storyboard.
   */
  function move(row, delta) {
    const mine = rows.filter((b) => b.slot === row.slot);
    const at = mine.findIndex((b) => b.id === row.id);
    const to = at + delta;
    if (at < 0 || to < 0 || to >= mine.length) return;
    const other = mine[to];
    run(row.id, async () => {
      await api.adminUpdateBanner(row.id, { rank: other.rank });
      await api.adminUpdateBanner(other.id, { rank: row.rank });
    });
  }

  return (
    <div className="admin-body">
      {error && <p className="error">{error}</p>}

      <div className="info-msg admin-note-box">
        These are the two billboards at the top of <strong>Explore</strong> — the
        first screen anybody lands on. With none of them showing, the page falls
        back to cards built from your workflow list, which is what it did before
        this screen existed. Nothing here is lost by hiding it.
      </div>

      <section className="card admin-card">
        <div className="admin-section-head">
          <div>
            <h2 className="admin-h2">Explore banners</h2>
            <p className="muted tiny admin-group-blurb">
              <strong>Rotating (left)</strong> is the wide one that changes every
              few seconds — up to {data.max_per_slot} cards, in the order below.{" "}
              <strong>Fixed (right)</strong> is the pale one beside it and shows
              one card. Pictures are scaled to {data.image_max_px}px and may be
              PNG, JPEG or WEBP.
            </p>
          </div>
          {/* Not a ghost when it IS the action — same reasoning as the Offers
              section's own button. Cancel stays a ghost. */}
          <button
            className={`btn small ${open ? "ghost" : ""}`}
            onClick={() => {
              setFormError("");
              setOpen((o) => !o);
            }}
          >
            {open ? "Cancel" : "＋ New banner"}
          </button>
        </div>

        {open && (
          <div className="admin-rollout">
            <label className="admin-rollout-row">
              <span className="muted tiny">Where it goes</span>
              <select
                className="admin-search"
                value={form.slot}
                onChange={(e) => update({ slot: e.target.value })}
              >
                {(data.slots || []).map((slot) => (
                  <option key={slot} value={slot}>
                    {SLOT_LABEL[slot] || slot}
                  </option>
                ))}
              </select>
            </label>
            <label className="admin-rollout-row">
              <span className="muted tiny">
                Small line above the heading (optional)
              </span>
              <input
                className="admin-search"
                maxLength={limits.kicker}
                value={form.kicker}
                placeholder="Limited offer"
                onChange={(e) => update({ kicker: e.target.value })}
              />
            </label>
            <label className="admin-rollout-row wide">
              <span className="muted tiny">Heading</span>
              <input
                className="admin-search"
                maxLength={limits.title}
                value={form.title}
                placeholder="Everything from one script"
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
                maxLength={limits.body}
                value={form.body}
                placeholder="Plan it, board it, block the motion, render the cut — in one place."
                onChange={(e) => update({ body: e.target.value })}
              />
            </label>
            <label className="admin-rollout-row">
              <span className="muted tiny">Button words (optional)</span>
              <input
                className="admin-search"
                maxLength={limits.cta_label}
                value={form.cta_label}
                placeholder="Open"
                onChange={(e) => update({ cta_label: e.target.value })}
              />
            </label>
            <label className="admin-rollout-row">
              <span className="muted tiny">The button opens</span>
              <select
                className="admin-search"
                value={form.target}
                onChange={(e) => update({ target: e.target.value })}
              >
                <option value={TARGET_NONE}>Nothing — no button</option>
                {WORKFLOWS.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.label}
                  </option>
                ))}
                <option value={TARGET_LINK}>A web address…</option>
              </select>
            </label>
            {form.target === TARGET_LINK && (
              <label className="admin-rollout-row wide">
                <span className="muted tiny">The address</span>
                <input
                  className="admin-search"
                  value={form.link}
                  placeholder="https://example.com/launch"
                  onChange={(e) => update({ link: e.target.value })}
                />
              </label>
            )}

            {/* ⚠ NOT AN `admin-rollout-row`. That class is the form GRID's cell
                and lays its children out in a column, so a sentence with a
                <strong> in the middle came out as three stacked lines. */}
            <p className="muted tiny admin-banner-hint">
              The picture is added after this — every banner gets its own{" "}
              <strong>Add picture</strong> button in the list below.
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
                      api.adminCreateBanner({
                        slot: form.slot,
                        kicker: form.kicker,
                        title: form.title,
                        body: form.body,
                        cta_label: form.cta_label,
                        cta_target: targetOf(form),
                        // Newest card goes to the BACK of its slot, which is
                        // where somebody adding a fourth one expects it. The
                        // arrows move it from there.
                        rank: rows.length,
                      }),
                    { silent: true }
                  );
                  // ⚠ ONLY CLOSES WHEN IT WORKED. Closing regardless loses every
                  // field that was filled in, so the person retypes the whole
                  // banner just to find out what was wrong with it.
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
            if (file && id) run(id, () => api.adminUploadBannerImage(id, file));
          }}
        />

        {(data.slots || []).map((slot) => {
          const mine = rows.filter((b) => b.slot === slot);
          return (
            <div key={slot}>
              <h3 className="admin-h3">{SLOT_LABEL[slot] || slot}</h3>
              {mine.length === 0 ? (
                <p className="muted tiny">
                  Nothing here — Explore draws its built-in card in this slot.
                </p>
              ) : (
                <ul className="admin-banner-list">
                  {mine.map((b, i) => (
                    <li className="admin-banner-row" key={b.id}>
                      <span className="admin-banner-pic">
                        {b.image_url ? (
                          <img src={api.absoluteUrl(b.image_url)} alt="" />
                        ) : (
                          <span className="muted tiny">No picture</span>
                        )}
                      </span>

                      <span className="admin-banner-text">
                        <span className="admin-banner-kicker">
                          {b.kicker || "—"}
                        </span>
                        <span className="admin-banner-title">{b.title}</span>
                        <span className="muted tiny">{b.body || "No line"}</span>
                        <span className="muted tiny">
                          {b.cta_label && b.cta_target
                            ? `Button: ${b.cta_label} → ${b.cta_target}`
                            : "No button"}
                        </span>
                      </span>

                      {/* ⚠ ORDER IS THE MEANING IN THIS LIST — RULEBOOK E6.
                          Disabled at either end, and `move` no-ops there too. */}
                      <span className="admin-banner-order">
                        <button
                          className="btn ghost small"
                          title="Move up"
                          disabled={i === 0 || busy === b.id}
                          onClick={() => move(b, -1)}
                        >
                          ↑
                        </button>
                        <button
                          className="btn ghost small"
                          title="Move down"
                          disabled={i === mine.length - 1 || busy === b.id}
                          onClick={() => move(b, 1)}
                        >
                          ↓
                        </button>
                      </span>

                      <span className="admin-banner-acts">
                        <span className={`badge ${b.active ? "ok" : ""}`}>
                          {b.active ? "Showing" : "Hidden"}
                        </span>
                        <button
                          className="btn ghost small"
                          disabled={busy === b.id}
                          onClick={() => {
                            uploadFor.current = b.id;
                            fileRef.current?.click();
                          }}
                        >
                          {busy === b.id
                            ? "Working…"
                            : b.has_image
                              ? "Replace picture"
                              : "📁 Add picture"}
                        </button>
                        {b.has_image && (
                          <button
                            className="btn ghost small"
                            disabled={busy === b.id}
                            onClick={() =>
                              run(b.id, () => api.adminRemoveBannerImage(b.id))
                            }
                          >
                            Remove picture
                          </button>
                        )}
                        <button
                          className="btn ghost small"
                          disabled={busy === b.id}
                          onClick={() =>
                            run(b.id, () =>
                              api.adminUpdateBanner(b.id, { active: !b.active })
                            )
                          }
                        >
                          {b.active ? "Hide" : "Show"}
                        </button>
                        {/* ⚠ DELETE ASKS FIRST AND HIDE DOES NOT, because only
                            one of them is reversible. Deleting takes the
                            picture with it. */}
                        {confirming === b.id ? (
                          <>
                            <button
                              className="btn small"
                              disabled={busy === b.id}
                              onClick={() => {
                                setConfirming("");
                                run(b.id, () => api.adminDeleteBanner(b.id));
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
                            disabled={busy === b.id}
                            onClick={() => setConfirming(b.id)}
                          >
                            Delete
                          </button>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </section>
    </div>
  );
}
