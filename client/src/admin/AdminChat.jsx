// AdminChat.jsx — the ✨ AI Editor's own tab: where it opens, what it costs a
// customer, and how careful it is.
//
// ---------------------------------------------------------------------------
// ⚠ ONE SCREEN, THREE OWNERS, AND THIS SCREEN OWNS ONE OF THEM.
// ---------------------------------------------------------------------------
// An operator thinks "the chat settings" is one page, so it is drawn as one
// page. What it edits lives in three stores that each already had a reason to
// exist, and this writes THROUGH them rather than keeping copies:
//
//   Is it on, and for whom?   →  the Features tab's registry (`cap.editor-chat`).
//                                SHOWN here, changed there — two editors for one
//                                row is how two screens end up disagreeing about
//                                which of them saved last.
//   How many messages does
//   a tier get per month?     →  that tier's `limits.chat_turns`, written through
//                                `billing.save_tier`. So it lands on the Pricing
//                                tab, on `/billing/tiers`, on the pricing card and
//                                in `usage.check` at once, because there is only
//                                one number.
//   How does it behave?       →  `server/chat_settings.py`. This screen's own.
//
// A `turn_limits` map in the chat's own store would have been fewer lines and a
// number the pricing page did not know about — and the first customer to hit it
// would have been reading a limit nobody advertised. See the rule at the top of
// `server/usage.py`.
//
// ⚠ THE BOUNDS COME FROM THE SERVER, NOT FROM HERE. `limits` on the payload
// carries every number's min and max, and the inputs read them. A second copy in
// this file would be a second opinion about what is legal: the browser would
// accept 2000 and the server would quietly store 60, which is a settings page
// that lies about what it saved.
import { useCallback, useEffect, useState } from "react";
import * as api from "../api.js";
import { formatDateTime } from "./format.js";

/** What each rail does, in the words an operator needs to decide. */
const RAILS = [
  {
    id: "ask_on_spend",
    label: "Ask before anything that costs money",
    note:
      "Video renders, generated images and spoken voiceover. The chat asks even " +
      "when the customer was perfectly clear. Turning this off does NOT let it " +
      "spend on its own — the priced confirmation is still the only door.",
  },
  {
    id: "ask_on_destructive",
    label: "Ask before deleting or overwriting",
    note:
      "The chat says what would go and how much of it, and waits. With this off " +
      "it proposes deletions straight away — still as a plan the customer has to " +
      "press Apply on, and Undo still puts it back.",
  },
  {
    id: "allow_paid_passes",
    label: "Let the chat offer to start paid work",
    note:
      "Off by default. Even on, the chat can only OPEN the priced confirmation " +
      "that ✨ Animate already uses — it can never start a render itself.",
  },
];

const NUMBERS = [
  {
    id: "transcript_keep",
    label: "Messages sent as context",
    note:
      "How much of the conversation rides along on each message. Higher is a " +
      "chat with a longer memory and a bigger bill — every one of these is " +
      "re-sent, and paid for, on every single turn.",
  },
  {
    id: "max_turns_per_session",
    label: "Messages per conversation",
    note:
      "A runaway guard, not a meter — clearing the chat resets it. 0 means no " +
      "ceiling beyond the monthly allowance below.",
  },
  {
    id: "shot_detail_limit",
    label: "Shots described in full",
    note:
      "Past this, a long film is summarised instead of listed shot by shot. The " +
      "single biggest lever on what one message costs on a feature-length project.",
  },
];

export default function AdminChat() {
  const [row, setRow] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  // The turn allowances, held locally while they are being typed. ⚠ NOT SAVED
  // PER KEYSTROKE: a number field saved on change writes "2", then "20", then
  // "200" — three tier writes and three audit records for one edit.
  const [turns, setTurns] = useState({});
  const [greeting, setGreeting] = useState("");
  // ⚠ HELD WHILE THE SLIDER IS MOVING, SAVED WHEN IT IS LET GO — the same rule
  // the Features tab's rollout percentage follows. A save on every `change` is
  // one audit record per pixel dragged.
  const [opacity, setOpacity] = useState(100);
  const [blur, setBlur] = useState(0);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    api
      .adminGetChat()
      .then((r) => {
        setRow(r);
        setGreeting(r.settings?.greeting || "");
        setOpacity(r.settings?.opacity ?? 100);
        setBlur(r.settings?.blur ?? 0);
        setTurns(
          Object.fromEntries(
            (r.tiers || []).map((t) => [t.id, t.turns === null ? "" : String(t.turns)])
          )
        );
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  /** Every write funnels through here: one place that applies the answer. */
  async function run(what, call) {
    setBusy(what);
    setError("");
    try {
      const saved = await call();
      setRow(saved);
      setGreeting(saved.settings?.greeting || "");
      setOpacity(saved.settings?.opacity ?? 100);
      setBlur(saved.settings?.blur ?? 0);
      setTurns(
        Object.fromEntries(
          (saved.tiers || []).map((t) => [t.id, t.turns === null ? "" : String(t.turns)])
        )
      );
    } catch (e) {
      setError(e.message);
      // Put the fields back to what the server actually holds — a rejected save
      // must not leave a form showing a setting nobody has.
      load();
    } finally {
      setBusy("");
    }
  }

  const save = (fields) => run(Object.keys(fields)[0], () => api.adminSaveChat(fields));

  if (loading || !row) {
    return (
      <div className="admin-body">
        <div className="card admin-card">
          <p className="muted">{error || "Loading…"}</p>
        </div>
      </div>
    );
  }

  const s = row.settings || {};
  const bounds = row.limits || {};
  const feature = row.feature || {};
  const featureOff = feature.status && feature.status !== "live";

  function saveTurns() {
    const limits = {};
    for (const tier of row.tiers || []) {
      const raw = (turns[tier.id] ?? "").trim();
      // ⚠ EMPTY IS UNLIMITED, ZERO IS BANNED, AND THEY ARE DIFFERENT ANSWERS.
      // Coercing a blank box to 0 is the most expensive typo available on this
      // screen — it would silently take the chat away from a paying tier.
      limits[tier.id] = raw === "" ? null : Math.max(0, parseInt(raw, 10) || 0);
    }
    run("limits", () => api.adminSaveChatLimits(limits));
  }

  const turnsDirty = (row.tiers || []).some((t) => {
    const raw = (turns[t.id] ?? "").trim();
    const now = raw === "" ? null : Math.max(0, parseInt(raw, 10) || 0);
    return now !== (t.turns ?? null);
  });

  const greetingDirty = greeting.trim() !== (s.greeting || "");

  return (
    <div className="admin-body">
      {error && <p className="error">{error}</p>}

      <div className="info-msg admin-note-box">
        The ✨ AI Editor is the chat inside the Video Editor. A customer types
        what they want changed and it answers, asks a question, or hands them an
        edit to approve. <strong>It never spends on its own</strong> — a render
        still goes through the same priced confirmation as ✨ Animate.
      </div>

      {/* ============================================ is it switched on ==== */}
      <section className="card admin-card">
        <div className="admin-section-head">
          <div>
            <h2 className="admin-h2">Switched on?</h2>
            <p className="muted tiny admin-group-blurb">
              Changed on the <strong>Features</strong> tab, where every other
              capability lives. Shown here so this page is not the one screen
              that cannot tell you whether the thing it configures is running.
            </p>
          </div>
        </div>
        <div className="admin-rollout">
          <div className="admin-rollout-row">
            <span className="muted tiny">Status</span>
            <span className={featureOff ? "muted" : ""}>
              {featureOff ? `⏸ ${feature.status}` : "● Live"} — {feature.label}
            </span>
          </div>
          <div className="admin-rollout-row">
            <span className="muted tiny">Who</span>
            <span className="muted">
              {feature.rollout?.mode === "all" || !feature.rollout?.mode
                ? "Everyone"
                : feature.rollout.mode}
              {feature.min_tier ? ` · needs ${feature.min_tier} or higher` : ""}
            </span>
          </div>
        </div>
      </section>

      {/* ================================================ where it opens === */}
      <section className="card admin-card">
        <div className="admin-section-head">
          <div>
            <h2 className="admin-h2">Where the panel opens</h2>
            <p className="muted tiny admin-group-blurb">
              Both are built. The ✨ button in the sidebar is in the same place
              either way — this is only where the panel it opens sits.
            </p>
          </div>
        </div>
        <div className="admin-rollout">
          {(row.docks || []).map((dock) => (
            <label key={dock.id} className="admin-rollout-row wide admin-chat-dock">
              <input
                type="radio"
                name="ec-dock"
                checked={s.dock === dock.id}
                disabled={!!busy}
                onChange={() => save({ dock: dock.id })}
              />
              <span>
                <strong>{dock.label}</strong>
                <span className="muted tiny"> — {dock.note}</span>
              </span>
            </label>
          ))}
          {/* ⚠ SAID ONCE, HERE, BECAUSE IT IS TRUE OF ALL THREE. Every dock can
              be resized by the customer — the two pinned ones by the edge that
              faces the editor, the floating one by its bottom-right corner —
              and only the floating one can be picked up and moved. Putting that
              in each dock's own note would have been three copies of one
              sentence that then disagreed the first time one changed. */}
          <p className="muted tiny admin-rollout-note">
            Whichever you pick, the customer can drag the panel wider or narrower
            and it is remembered in their browser. <strong>Only the floating
            window can be moved</strong> — the other two are pinned to an edge.
          </p>
        </div>
      </section>

      {/* ============================================== how see-through it is = */}
      <section className="card admin-card">
        <div className="admin-section-head">
          <div>
            <h2 className="admin-h2">How see-through the panel is</h2>
            <p className="muted tiny admin-group-blurb">
              At 100% it is a solid panel. At 0% it has no background at all and
              the film shows straight through the words. <strong>Judge it with
              the editor open in both themes</strong> — the light theme is a
              white panel over a near-white page, so it needs a much lower number
              than the dark one before anything looks different.{" "}
              <strong>This is yours, not theirs</strong>: the editor has no slider
              for it, so a panel nobody can read is not something a customer can
              do to themselves.
            </p>
          </div>
        </div>
        <div className="admin-rollout">
          <label className="admin-rollout-row wide">
            <span className="muted tiny">Panel solidity</span>
            <span className="admin-pct">
              <input
                type="range"
                min={bounds.opacity?.min ?? 0}
                max={bounds.opacity?.max ?? 100}
                step={5}
                value={opacity}
                disabled={!!busy}
                onChange={(e) => setOpacity(Number(e.target.value))}
                onMouseUp={() => save({ opacity })}
                onTouchEnd={() => save({ opacity })}
                onKeyUp={() => save({ opacity })}
                title="100% is solid. Lower lets the timeline show through the chat."
              />
              <span className="admin-pct-num">{opacity}%</span>
            </span>
          </label>
          {/* ⚠ THE SECOND HALF OF THE SAME DECISION, AND IT IS A SLIDER FOR
              THE SAME REASON THE FIRST ONE IS. Blur shipped hard-coded at 16px
              (invisible in the light theme, where white frosts onto white), was
              then removed entirely (and the text became hard to read at low
              opacity) — one number picked for every screen, wrong twice. It only
              does anything while the panel is see-through, which is why it is
              disabled and says so at 100%. */}
          <label className="admin-rollout-row wide">
            <span className="muted tiny">Blur behind it</span>
            <span className="admin-pct">
              <input
                type="range"
                min={bounds.blur?.min ?? 0}
                max={bounds.blur?.max ?? 40}
                step={2}
                value={blur}
                disabled={!!busy || opacity >= 100}
                onChange={(e) => setBlur(Number(e.target.value))}
                onMouseUp={() => save({ blur })}
                onTouchEnd={() => save({ blur })}
                onKeyUp={() => save({ blur })}
                title="Softens the film behind the panel so the chat stays readable. Does nothing while the panel is solid."
              />
              <span className="admin-pct-num">{blur}px</span>
            </span>
          </label>
          {/* ⚠ THE WHOLE OPACITY RANGE IS OPEN, AND THAT IS THE POINT — the floor
              used to be 40, chosen off the DARK theme, and in the light theme 40%
              white over a white page is a difference nobody can see. Judging it
              is the operator's job now, so the screen says what the low end
              actually costs instead of pretending a number is unsafe. */}
          <p className="muted tiny admin-rollout-note">
            Solidity is the whole story on its own — at 60% the panel covers 60%
            of what is behind it, the same in both themes.{" "}
            <strong>Blur is what makes the words readable</strong> once you go
            low: it softens the film underneath instead of covering it, so the
            chat stays legible over a busy timeline.{" "}
            {opacity >= 100
              ? "It is switched off here because a solid panel has nothing behind it to blur."
              : "Try 12–20px if the text is hard to read at this solidity."}
          </p>
        </div>
      </section>

      {/* ============================================ the monthly allowance = */}
      <section className="card admin-card">
        <div className="admin-section-head">
          <div>
            <h2 className="admin-h2">Messages per month, by tier</h2>
            <p className="muted tiny admin-group-blurb">
              One message is one call to the model. This is the same{" "}
              <code>chat_turns</code> limit the Pricing tab edits — there is only
              one number, so a change here shows up on the pricing card too.{" "}
              <strong>Leave a box empty for unlimited.</strong> Zero means the
              tier cannot use the chat at all.
            </p>
          </div>
        </div>
        <div className="admin-rollout">
          {(row.tiers || [])
            .filter((t) => !t.archived)
            .map((tier) => (
              <label key={tier.id} className="admin-rollout-row">
                <span className="muted tiny">{tier.name}</span>
                <input
                  className="admin-search admin-chat-num"
                  type="number"
                  min={0}
                  placeholder="Unlimited"
                  value={turns[tier.id] ?? ""}
                  disabled={!!busy}
                  onChange={(e) =>
                    setTurns((cur) => ({ ...cur, [tier.id]: e.target.value }))
                  }
                />
              </label>
            ))}
          <div className="admin-brand-acts">
            <button
              type="button"
              className="btn primary"
              disabled={!turnsDirty || !!busy}
              onClick={saveTurns}
            >
              {busy === "limits" ? "Saving…" : "Save allowances"}
            </button>
          </div>
        </div>
      </section>

      {/* ====================================================== the rails === */}
      <section className="card admin-card">
        <div className="admin-section-head">
          <div>
            <h2 className="admin-h2">How careful it is</h2>
            <p className="muted tiny admin-group-blurb">
              Asking before it acts is what makes this different from every other
              AI editor on the market. These default on, and they should stay on
              unless you have a reason.
            </p>
          </div>
        </div>
        <div className="admin-rollout">
          {RAILS.map((rail) => (
            <label key={rail.id} className="admin-rollout-row wide admin-chat-dock">
              <input
                type="checkbox"
                checked={!!s[rail.id]}
                disabled={!!busy}
                onChange={(e) => save({ [rail.id]: e.target.checked })}
              />
              <span>
                <strong>{rail.label}</strong>
                <span className="muted tiny"> — {rail.note}</span>
              </span>
            </label>
          ))}
        </div>
      </section>

      {/* ============================================== cost and behaviour == */}
      <section className="card admin-card">
        <div className="admin-section-head">
          <div>
            <h2 className="admin-h2">What one message costs</h2>
            <p className="muted tiny admin-group-blurb">
              Every one of these decides how big the prompt is, and the prompt is
              the bill. Applying an edit is free — only the answer is paid for.
            </p>
          </div>
        </div>
        <div className="admin-rollout">
          {NUMBERS.map((field) => {
            const bound = bounds[field.id] || {};
            return (
              <label key={field.id} className="admin-rollout-row wide">
                <span>
                  <strong>{field.label}</strong>
                  <span className="muted tiny"> — {field.note}</span>
                </span>
                <input
                  className="admin-search admin-chat-num"
                  type="number"
                  min={bound.min}
                  max={bound.max}
                  value={s[field.id] ?? ""}
                  disabled={!!busy}
                  onChange={(e) => save({ [field.id]: Number(e.target.value) })}
                />
                <span className="muted tiny">
                  {bound.min}–{bound.max}
                </span>
              </label>
            );
          })}

          {/* ⚠ NOT VALIDATED AGAINST A LIST OF MODELS, and the server says why:
              every provider names its models differently and the OpenAI-shaped
              adapter can point at anything. A whitelist here would go stale in
              the direction that hurts — refusing the id you are moving to. */}
          <label className="admin-rollout-row wide">
            <span>
              <strong>Model</strong>
              <span className="muted tiny">
                {" "}
                — leave empty to use whatever the Director is wired to, which is
                the one already known to answer correctly here.
              </span>
            </span>
            <input
              className="admin-search"
              placeholder="(the Director's model)"
              defaultValue={s.model || ""}
              disabled={!!busy}
              onBlur={(e) => {
                if (e.target.value.trim() !== (s.model || "")) {
                  save({ model: e.target.value.trim() });
                }
              }}
            />
          </label>
        </div>
      </section>

      {/* ==================================================== the greeting == */}
      <section className="card admin-card">
        <div className="admin-section-head">
          <div>
            <h2 className="admin-h2">First line in an empty chat</h2>
            <p className="muted tiny admin-group-blurb">
              Leave empty for the built-in wording, which is written to match the
              rest of the editor's voice.
            </p>
          </div>
        </div>
        <div className="admin-rollout admin-brand-form">
          <label className="admin-rollout-row wide">
            <input
              className="admin-search"
              value={greeting}
              maxLength={row.greeting_max || 240}
              placeholder="Tell me what you want changed and I'll show you the edit first…"
              disabled={!!busy}
              onChange={(e) => setGreeting(e.target.value)}
            />
            <span className="muted tiny">
              {greeting.length}/{row.greeting_max || 240}
            </span>
          </label>
          <div className="admin-brand-acts">
            <button
              type="button"
              className="btn primary"
              disabled={!greetingDirty || !!busy}
              onClick={() => save({ greeting: greeting.trim() })}
            >
              {busy === "greeting" ? "Saving…" : "Save greeting"}
            </button>
          </div>
        </div>
      </section>

      {s.updated_at && (
        <p className="muted tiny">
          Last changed {formatDateTime(s.updated_at)}
          {s.updated_by ? ` by ${s.updated_by}` : ""}.
        </p>
      )}
    </div>
  );
}
