import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import Icon from "./Icon.jsx";

// Same shape as the storyboard library: "Recent" highlights the newest, "All"
// lists everything (including that one).
const RECENT_COUNT = 1;
// Dimmed placeholder cards while loading, so the page reads as a gallery
// waiting to be filled rather than bare text.
const GHOST_COUNT = { recent: 1, all: 3 };

function formatDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

// Plan & Script — the first step of the pipeline: decide WHAT to make.
//
// Two screens:
//   "library" — every planning session, so returning users land on their work
//               (same pattern as Your Storyboards).
//   "session" — the conversation with the strategist agent, the channel it
//               researched, and the calendar it produced.
//
// Everything the user types is persisted server-side on the way in, so a
// refresh mid-conversation loses nothing.

// The common spans. Anything else is typed in via "Custom…", which the server
// still caps at 12 — past a year a content plan is a guess, not a schedule.
const MONTH_CHOICES = [1, 2, 3, 6, 9, 12];
const MAX_MONTHS = 12;

// Every weekly rate from once to daily, then the sub-weekly ones. 7 is spelled
// out rather than only offered as "Daily" so the list reads as one scale.
const CADENCES = [
  "1 per week",
  "2 per week",
  "3 per week",
  "4 per week",
  "5 per week",
  "6 per week",
  "7 per week (daily)",
  "3 per month",
  "2 per month",
  "1 per month",
];

const CUSTOM = "__custom__";

// Opening prompts. A blank chat box is the hardest part of any agent UI — these
// are the questions this tool is actually good at, phrased as the user would.
const STARTERS = [
  "I run a YouTube channel about mythology. Plan my next 3 months.",
  "I'm a 3D artist. What should I post to get client work?",
  "Plan 1 month of short-form content for my small business.",
  "I edit videos for clients. Help me build an audience on Instagram.",
];

const GOAL_TONE = {
  reach: "goal-reach",
  engagement: "goal-engagement",
  conversion: "goal-conversion",
  retention: "goal-retention",
};

export default function PlanAndScript() {
  const [step, setStep] = useState("library");
  const [sessions, setSessions] = useState([]);
  const [plan, setPlan] = useState(null); // the open session
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  // True until the first list lands, so the sections show skeletons instead of
  // flashing "No plans yet" at someone who has plenty.
  const [loading, setLoading] = useState(true);

  // Chat
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef(null);

  // Channel
  const [channelUrl, setChannelUrl] = useState("");
  const [channelBusy, setChannelBusy] = useState(false);
  const [ytConfigured, setYtConfigured] = useState(null);

  // Plan generation. Each control keeps the DROPDOWN choice and the typed
  // custom value separately, so switching to Custom and back doesn't lose what
  // was picked, and switching away doesn't lose what was typed.
  const [monthChoice, setMonthChoice] = useState("3");
  const [customMonths, setCustomMonths] = useState("");
  const [cadenceChoice, setCadenceChoice] = useState("2 per week");
  const [customCadence, setCustomCadence] = useState("");
  const [generating, setGenerating] = useState(false);
  const [exporting, setExporting] = useState("");

  // What actually gets sent. Note the upper bound is clamped but the LOWER one
  // deliberately is not: an empty or junk Custom box must fall through as 0 so
  // the guard below catches it. Clamping it up to 1 would quietly generate a
  // one-month plan for someone who simply hadn't finished typing.
  const months = monthChoice === CUSTOM
    ? Math.min(MAX_MONTHS, parseInt(customMonths, 10) || 0)
    : Number(monthChoice);
  const cadence = (cadenceChoice === CUSTOM ? customCadence : cadenceChoice).trim();

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await api.listPlans());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSessions();
    api
      .youtubeConfigured()
      .then((r) => setYtConfigured(Boolean(r?.configured)))
      .catch(() => setYtConfigured(false));
  }, [loadSessions]);

  // Keep the newest message in view as the conversation grows.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [plan?.messages?.length, sending]);

  function open(detail) {
    setPlan(detail);
    setChannelUrl(detail?.channel?.input || "");
    // Reopen the controls on whatever this plan was last built with. A value
    // that isn't one of the presets lands in the Custom box rather than being
    // silently snapped to the nearest option.
    const m = detail?.plan?.months;
    if (m) {
      if (MONTH_CHOICES.includes(m)) {
        setMonthChoice(String(m));
      } else {
        setMonthChoice(CUSTOM);
        setCustomMonths(String(m));
      }
    }
    const c = detail?.plan?.cadence;
    if (c) {
      if (CADENCES.includes(c)) {
        setCadenceChoice(c);
      } else {
        setCadenceChoice(CUSTOM);
        setCustomCadence(c);
      }
    }
    setStep("session");
    setError("");
    setNotice("");
  }

  async function newSession() {
    setBusy(true);
    setError("");
    try {
      open(await api.createPlan());
      await loadSessions();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function openSession(id) {
    setBusy(true);
    setError("");
    try {
      open(await api.getPlan(id));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function send(text) {
    const message = (text ?? draft).trim();
    if (!message || sending || !plan) return;
    setSending(true);
    setError("");
    // Show the user's line immediately; the server is the source of truth and
    // replaces this the moment the reply lands.
    const optimistic = {
      ...plan,
      messages: [...(plan.messages || []), { role: "user", text: message, at: "" }],
    };
    setPlan(optimistic);
    setDraft("");
    try {
      const updated = await api.sendPlanMessage(plan.job_id, message);
      setPlan(updated);
      loadSessions();
    } catch (e) {
      // Roll the optimistic line back — the server didn't keep it either, so
      // leaving it on screen would misrepresent the transcript.
      setPlan((cur) => ({ ...cur, messages: plan.messages || [] }));
      setDraft(message);
      setError(e.message);
    } finally {
      setSending(false);
    }
  }

  async function researchChannel() {
    if (!channelUrl.trim() || !plan) return;
    setChannelBusy(true);
    setError("");
    setNotice("");
    try {
      const updated = await api.attachPlanChannel(plan.job_id, channelUrl.trim());
      setPlan(updated);
      const ch = updated.channel || {};
      setNotice(
        ch.available
          ? `Read ${ch.title} — ${Number(ch.subscribers).toLocaleString()} subscribers, ${ch.video_count} videos.`
          : ch.reason || "Couldn't read that channel."
      );
    } catch (e) {
      setError(e.message);
    } finally {
      setChannelBusy(false);
    }
  }

  async function generate() {
    if (!plan) return;
    setGenerating(true);
    setError("");
    try {
      setPlan(await api.generatePlan(plan.job_id, { months, cadence }));
      loadSessions();
    } catch (e) {
      setError(e.message);
    } finally {
      setGenerating(false);
    }
  }

  async function exportAs(format) {
    if (!plan) return;
    setExporting(format);
    setError("");
    try {
      await api.downloadPlan(plan.job_id, format);
    } catch (e) {
      setError(e.message);
    } finally {
      setExporting("");
    }
  }

  // Renames from the library card (`target` = a summary) and from the open
  // session header (no argument → the session being viewed).
  async function rename(target) {
    const subject = target || plan;
    if (!subject) return;
    const next = window.prompt("Name this plan", subject.title || "");
    if (next === null || !next.trim()) return;
    try {
      const updated = await api.renamePlan(subject.job_id, next.trim());
      // Only re-point the open session if that's what was renamed.
      if (plan?.job_id === subject.job_id) setPlan(updated);
      loadSessions();
    } catch (e) {
      setError(e.message);
    }
  }

  async function remove(id) {
    if (!window.confirm("Delete this plan? This can't be undone.")) return;
    try {
      await api.deletePlan(id);
      if (plan?.job_id === id) {
        setPlan(null);
        setStep("library");
      }
      loadSessions();
    } catch (e) {
      setError(e.message);
    }
  }

  // ---- Library ------------------------------------------------------------
  // Deliberately the SAME furniture as Your Storyboards and the animatic
  // library: New row, then "Recent", then "All", with the same cards, ghosts
  // and empty state. A workflow that invents its own gallery makes the app feel
  // like three apps.
  //
  // renderSection is a render FUNCTION, not a nested component — a component
  // declared in here would get a new identity every render and remount the
  // section on each keystroke (the same reason StoryboardLibrary does it).
  function renderPlanCard(s, section) {
    return (
      <div className="card lib-card" key={`${section}:${s.job_id}`}>
        <div
          className="lib-cover"
          onClick={() => openSession(s.job_id)}
          title="Open this plan"
        >
          <div className="lib-cover-empty">🗓️</div>
          {s.item_count > 0 && <span className="lib-badge">{s.item_count} uploads</span>}
        </div>

        <div className="lib-body">
          <div className="lib-title" onClick={() => openSession(s.job_id)} title={s.title}>
            {s.title}
          </div>

          <div className="lib-meta">
            {s.months > 0 && (
              <span className="chip">
                {s.months} month{s.months === 1 ? "" : "s"}
              </span>
            )}
            <span className="chip">
              {s.message_count} message{s.message_count === 1 ? "" : "s"}
            </span>
            {s.channel_title && <span className="chip">📺 {s.channel_title}</span>}
            {s.item_count === 0 && <span className="chip">no plan yet</span>}
          </div>

          <div className="lib-foot">
            <span className="tiny muted">{formatDate(s.created_at)}</span>
            <div className="lib-actions">
              <button
                type="button"
                className="lib-icon"
                title="Rename"
                onClick={() => rename(s)}
              >
                <Icon name="pencil" />
              </button>
              <button
                type="button"
                className="lib-icon"
                title="Delete this plan"
                onClick={() => remove(s.job_id)}
              >
                <Icon name="trash" />
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  function renderSection(section, title, hint, items) {
    const ghosts = GHOST_COUNT[section] || 1;
    return (
      <section className="lib-section" key={section}>
        <div className="lib-section-head">
          <h2 className="lib-section-title">{title}</h2>
          <span className="tiny muted">{hint}</span>
        </div>
        {loading ? (
          <div className="lib-grid lib-ghosts is-loading">
            {Array.from({ length: ghosts }, (_, i) => (
              <div className="card lib-card lib-ghost" key={i} aria-hidden="true">
                <div className="lib-cover lib-ghost-cover" />
                <div className="lib-body">
                  <div className="lib-ghost-line lib-ghost-title" />
                  <div className="lib-meta">
                    <span className="lib-ghost-chip" />
                    <span className="lib-ghost-chip" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="lib-grid">
            <div className="card lib-card lib-ghost-empty">
              <span className="lib-empty-ico">🗓️</span>
              <p className="lib-empty-text">
                No plans yet — hit <strong>New Plan</strong> and tell the agent
                what you make.
              </p>
            </div>
          </div>
        ) : (
          <div className="lib-grid">{items.map((s) => renderPlanCard(s, section))}</div>
        )}
      </section>
    );
  }

  const recent = sessions.slice(0, RECENT_COUNT);

  if (step === "library") {
    return (
      <div className="workflow-head-wrap sb-library">
        <div className="workflow-header">
          <span className="wf-icon">🗓️</span>
          <div>
            <h1 className="wf-title">Plan &amp; Script</h1>
            <p className="muted">
              Talk to a content strategist. Get a publishing calendar you can
              export to Excel or Word — before you make anything.
            </p>
          </div>
        </div>

        {error && <div className="error">{error}</div>}

        {/* New plan — always first, so starting is one click. */}
        <div className="lib-grid lib-new-row">
          <button type="button" className="card lib-new" onClick={newSession} disabled={busy}>
            <span className="lib-new-plus">+</span>
            <span className="lib-new-title">New Plan</span>
            <span className="tiny muted">
              {loading
                ? "Loading your plans…"
                : `${sessions.length} plan${sessions.length === 1 ? "" : "s"} created`}
            </span>
          </button>
        </div>

        {renderSection("recent", "Recent Plans", recent.length ? "Your latest plan" : "", recent)}
        {renderSection(
          "all",
          "All Plans",
          sessions.length ? `${sessions.length} in total` : "",
          sessions
        )}
      </div>
    );
  }

  // ---- Session ------------------------------------------------------------
  const messages = plan?.messages || [];
  // Declared here rather than with the other derived values because it needs
  // `messages`. A half-filled Custom box must not reach the server as a silent
  // default — better a disabled button than a plan for the wrong span.
  const canGenerate = messages.length > 0 && months >= 1 && Boolean(cadence);
  const built = plan?.plan || {};
  const items = built.items || [];
  const channel = plan?.channel || {};

  return (
    <div className="workflow-head-wrap plan-page">
      <div className="workflow-header">
        <span className="wf-icon">🗓️</span>
        <div>
          <h1 className="wf-title">{plan?.title || "Plan & Script"}</h1>
          <p className="muted">
            {items.length > 0
              ? `${items.length} uploads · ${built.months} month${built.months === 1 ? "" : "s"} · ${built.cadence}`
              : "Tell the agent what you make and who it's for."}
          </p>
        </div>
      </div>

      <div className="review-actions top-actions">
        <button className="btn" onClick={() => { setStep("library"); loadSessions(); }}>
          ← Your plans
        </button>
        <div className="review-actions-right">
          {/* Wrapped, not passed directly: onClick would hand rename() the
              click Event as its `target` argument. */}
          <button className="btn ghost" onClick={() => rename()}>
            Rename
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}
      {notice && <div className="info-msg">{notice}</div>}

      {/* Channel research */}
      <section className="card plan-channel">
        <label className="field">
          <span className="field-label">Your channel (optional)</span>
          <div className="plan-channel-row">
            <input
              value={channelUrl}
              placeholder="https://youtube.com/@yourchannel"
              onChange={(e) => setChannelUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && researchChannel()}
            />
            <button
              className="btn"
              onClick={researchChannel}
              disabled={channelBusy || !channelUrl.trim()}
            >
              {channelBusy ? "Reading…" : "Research"}
            </button>
          </div>
        </label>
        {ytConfigured === false && (
          <p className="muted tiny">
            The agent opens your channel page and reads it. For exact subscriber
            and view counts, add a YouTube API key on the server — without one it
            reads topics and titles, and won't state numbers it can't see.
          </p>
        )}
        {/* What was actually read, and from where — so the difference between
            "measured" and "read off the page" is visible, not implied. */}
        {channel.available && channel.source === "youtube_api" && (
          <p className="muted tiny">
            ✓ {channel.title} — {Number(channel.subscribers).toLocaleString()} subscribers ·{" "}
            {channel.video_count} videos
            {channel.cadence ? ` · currently ${channel.cadence}` : ""}
          </p>
        )}
        {channel.available && channel.source === "gemini_url_context" && (
          <p className="muted tiny">
            ✓ Read {channel.title || "your channel"} from the page
            {channel.recent_videos?.length
              ? ` — ${channel.recent_videos.length} recent titles`
              : ""}
            . Exact subscriber counts need a YouTube API key.
          </p>
        )}
      </section>

      {/* Conversation */}
      <section className="card plan-chat">
        <div className="plan-chat-log" ref={scrollRef}>
          {messages.length === 0 && (
            <div className="plan-starters">
              <p className="muted">Not sure where to start?</p>
              {STARTERS.map((s) => (
                <button key={s} className="btn small plan-starter" onClick={() => send(s)}>
                  {s}
                </button>
              ))}
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`plan-msg ${m.role === "user" ? "is-user" : "is-agent"}`}>
              <span className="plan-msg-who">{m.role === "user" ? "You" : "Agent"}</span>
              <div className="plan-msg-text">{m.text}</div>
            </div>
          ))}
          {sending && (
            <div className="plan-msg is-agent">
              <span className="plan-msg-who">Agent</span>
              <div className="plan-msg-text muted">
                <span className="spinner-inline" /> Thinking…
              </div>
            </div>
          )}
        </div>

        <div className="plan-chat-input">
          <textarea
            className="prompt-textarea"
            rows={2}
            value={draft}
            placeholder="Tell the agent about your channel, your audience, how often you can post…"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends; Shift+Enter is a newline. Standard for chat, and
              // the message box is short enough that Enter-to-send is expected.
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
          <button
            className="btn primary"
            onClick={() => send()}
            disabled={sending || !draft.trim()}
          >
            {sending ? "Sending…" : "Send"}
          </button>
        </div>
      </section>

      {/* Generate */}
      <section className="card plan-generate">
        <h2>Build the calendar</h2>
        <p className="muted tiny">
          Uses everything said above. You can regenerate after talking more.
        </p>
        <div className="plan-generate-row">
          <label className="field">
            <span className="field-label">Months</span>
            <select
              value={monthChoice}
              onChange={(e) => setMonthChoice(e.target.value)}
            >
              {MONTH_CHOICES.map((m) => (
                <option key={m} value={String(m)}>
                  {m} month{m === 1 ? "" : "s"}
                </option>
              ))}
              <option value={CUSTOM}>Custom…</option>
            </select>
            {monthChoice === CUSTOM && (
              <input
                type="number"
                min={1}
                max={MAX_MONTHS}
                value={customMonths}
                placeholder={`1–${MAX_MONTHS}`}
                onChange={(e) => setCustomMonths(e.target.value)}
              />
            )}
          </label>

          <label className="field">
            <span className="field-label">How often you publish</span>
            <select
              value={cadenceChoice}
              onChange={(e) => setCadenceChoice(e.target.value)}
            >
              {CADENCES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
              <option value={CUSTOM}>Custom…</option>
            </select>
            {cadenceChoice === CUSTOM && (
              <input
                value={customCadence}
                placeholder="e.g. 3 shorts + 1 long-form per week"
                maxLength={120}
                onChange={(e) => setCustomCadence(e.target.value)}
              />
            )}
          </label>

          <button
            className="btn primary"
            onClick={generate}
            disabled={generating || !canGenerate}
            title={
              messages.length === 0
                ? "Talk to the agent first"
                : !cadence
                ? "Say how often you publish"
                : months < 1
                ? `Enter a number of months (1–${MAX_MONTHS})`
                : undefined
            }
          >
            {generating ? (
              <>
                <span className="spinner-inline" /> Planning…
              </>
            ) : items.length ? (
              "Regenerate plan"
            ) : (
              "Generate plan"
            )}
          </button>
        </div>
      </section>

      {/* The calendar */}
      {items.length > 0 && (
        <>
          {built.summary && (
            <section className="card">
              <h2>Strategy</h2>
              <p>{built.summary}</p>
              {built.pillars?.length > 0 && (
                <ul className="plan-pillars">
                  {built.pillars.map((p) => (
                    <li key={p.name}>
                      <strong>{p.name}</strong>
                      {p.why ? ` — ${p.why}` : ""}
                    </li>
                  ))}
                </ul>
              )}
              {built.assumptions?.length > 0 && (
                <details className="plan-assumptions">
                  <summary className="muted tiny">
                    {built.assumptions.length} assumption
                    {built.assumptions.length === 1 ? "" : "s"} the agent made
                  </summary>
                  <ul>
                    {built.assumptions.map((a, i) => (
                      <li key={i} className="muted tiny">
                        {a}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </section>
          )}

          <div className="review-actions top-actions">
            <div className="review-actions-right">
              {["xlsx", "docx", "csv"].map((f) => (
                <button
                  key={f}
                  className="btn"
                  onClick={() => exportAs(f)}
                  disabled={Boolean(exporting)}
                >
                  {exporting === f ? "Preparing…" : `⬇ ${f.toUpperCase()}`}
                </button>
              ))}
            </div>
          </div>

          <div className="plan-grid">
            {items.map((it, i) => (
              <article key={i} className="card plan-item">
                <header className="plan-item-head">
                  <span className="plan-slot">{it.slot}</span>
                  {it.goal && (
                    <span className={`plan-chip ${GOAL_TONE[it.goal] || ""}`}>{it.goal}</span>
                  )}
                  {it.effort && <span className="plan-chip">{it.effort} effort</span>}
                </header>
                <h3 className="plan-item-title">{it.title}</h3>
                {it.hook && (
                  <p className="plan-hook">
                    <span className="plan-label">Hook</span>
                    {it.hook}
                  </p>
                )}
                <p className="muted tiny">
                  {[it.format, it.pillar].filter(Boolean).join(" · ")}
                </p>
                {it.outline?.length > 0 && (
                  <ol className="plan-outline">
                    {it.outline.map((b, j) => (
                      <li key={j}>{b}</li>
                    ))}
                  </ol>
                )}
                {it.cta && (
                  <p className="tiny">
                    <span className="plan-label">CTA</span>
                    {it.cta}
                  </p>
                )}
                {it.keywords?.length > 0 && (
                  <p className="muted tiny plan-keywords">{it.keywords.join(" · ")}</p>
                )}
              </article>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
