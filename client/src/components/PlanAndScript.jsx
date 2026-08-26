import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api.js";
import Icon from "./Icon.jsx";
import LibrarySection, { LibraryRow, matchesFilter } from "./LibraryList.jsx";
import PlanExportPreview from "./PlanExportPreview.jsx";
import PlanQuestions from "./PlanQuestions.jsx";
import PlanLanguageModal, { LANGUAGES } from "./PlanLanguageModal.jsx";
import PlanScriptModal, { usageLine } from "./PlanScriptModal.jsx";
// Pure, so node can import it — tests/plan_script_check.py drives it directly.
import { formatRuntime, secondsFromFormat } from "../plan/script_length.js";

import WorkflowIcon from "./WorkflowIcon.jsx";
// Same shape as every other library — and that shape is now ONE section of
// rows, not two grids of cards. "Recent Plans" used to hold the newest plan
// and "All Plans" repeated the whole list underneath it. The heading stays;
// it lists EVERY plan, newest first. See LibraryList.jsx.
//
// Dimmed placeholder rows while loading, so the page reads as a list waiting
// to be filled rather than bare text.
const GHOST_ROWS = 5;

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

// A stored language is either one of the presets or whatever the user typed,
// so an unknown value is shown as-is rather than swallowed.
function languageLabel(id) {
  const known = LANGUAGES.find((l) => l.id === id);
  return known ? known.label : id;
}

const GOAL_TONE = {
  reach: "goal-reach",
  engagement: "goal-engagement",
  conversion: "goal-conversion",
  retention: "goal-retention",
};

// Lengths for a script asked for OUTSIDE the calendar. A calendar row doesn't
// need this control because its `format` field already says how long it is —
// see secondsFromFormat.
const SCRIPT_LENGTHS = [
  [15, "15s — hook only"],
  [30, "30s short"],
  [45, "45s short"],
  [60, "1 minute"],
  [90, "90 seconds"],
  [180, "3 minutes"],
  [300, "5 minutes"],
  [480, "8 minutes"],
  [600, "10 minutes"],
  [900, "15 minutes"],
];

export default function PlanAndScript({ onOpenStoryboard }) {
  const [step, setStep] = useState("library");
  const [sessions, setSessions] = useState([]);
  // What's typed in the library's Filter box. Purely a VIEW of `sessions` —
  // nothing is re-fetched — so someone with a year of plans finds one by name
  // instead of scrolling.
  const [libQuery, setLibQuery] = useState("");
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
  // Set when the user dismisses a question panel, so it stays dismissed until
  // the agent asks something new rather than reappearing on every render.
  const [dismissedAt, setDismissedAt] = useState(null);

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
  // Which format's preview is open ("" / null = none), and which xlsx sheet
  // that preview is showing.
  const [previewFormat, setPreviewFormat] = useState(null);
  const [previewSheet, setPreviewSheet] = useState("Calendar");
  // The language currently chosen, held in state so it's visible in the row
  // BEFORE anything has been generated and stays visible afterwards.
  const [language, setLanguage] = useState("english");
  // The picker is opened two ways and does different things:
  //   "generate" — from the Generate button; confirming builds the plan.
  //   "pick"     — from the Language field; confirming just sets the language.
  // null = closed.
  const [langMode, setLangMode] = useState(null);

  // Scripts. `writingFor` is what's currently being written — an item index, or
  // the string "brief" for the standalone box — so exactly one button can show
  // a spinner and the rest disable. A plain boolean lit up every card.
  const [writingFor, setWritingFor] = useState(null);
  const [openScriptId, setOpenScriptId] = useState(null);
  const [scriptBusy, setScriptBusy] = useState(false);
  const [scriptBrief, setScriptBrief] = useState("");
  const [scriptSeconds, setScriptSeconds] = useState(60);

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

  // Escape closes the export preview — expected of any modal, and the overlay
  // click alone isn't enough for keyboard users.
  useEffect(() => {
    if (!previewFormat) return;
    const onKey = (e) => {
      if (e.key === "Escape") setPreviewFormat(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [previewFormat]);

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
    // Reopen on the language this plan was written in, so the row reflects the
    // board on screen rather than resetting to English.
    setLanguage(detail?.plan?.language || "english");
    setStep("session");
    setError("");
    setNotice("");
  }

  // "New Plan" opens an UNSAVED session — nothing is written until the user
  // actually does something. Creating the record on the button click meant
  // opening the screen, changing your mind, and leaving an empty "Untitled
  // plan" behind in the library every time.
  const EMPTY_SESSION = {
    job_id: null,
    title: "",
    messages: [],
    channel: {},
    plan: {},
  };

  function newSession() {
    setError("");
    setNotice("");
    setChannelUrl("");
    setDraft("");
    open({ ...EMPTY_SESSION });
  }

  // Called by the first action that needs somewhere to store itself. Returns
  // the session id, creating the record on the spot the first time.
  async function ensureSession() {
    if (plan?.job_id) return plan.job_id;
    const created = await api.createPlan();
    // Keep whatever the user has already put on screen; take the identity.
    setPlan((cur) => ({ ...created, ...cur, job_id: created.job_id, title: created.title }));
    loadSessions();
    return created.job_id;
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
      // First message is what makes the session real.
      const id = await ensureSession();
      const updated = await api.sendPlanMessage(id, message);
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

  // Picked answers become an ordinary chat message. Composing them into plain
  // prose (rather than posting a hidden structured payload) keeps the
  // transcript readable and means the agent handles them like any other reply.
  function submitAnswers(answers) {
    const text = answers
      .map((a) => `${a.header}: ${a.value}`)
      .join("\n");
    send(text);
  }

  async function researchChannel() {
    if (!channelUrl.trim() || !plan) return;
    setChannelBusy(true);
    setError("");
    setNotice("");
    try {
      // Attaching a channel counts as doing something, so save the session.
      const id = await ensureSession();
      const updated = await api.attachPlanChannel(id, channelUrl.trim());
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

  // Generate asks for the language first — the plan's titles and hooks get
  // published as written, so the language isn't a detail to bury in settings.
  async function generate(chosen) {
    if (!plan) return;
    const lang = chosen || language;
    setLanguage(lang); // keep the row showing what was actually used
    setGenerating(true);
    setError("");
    try {
      setPlan(await api.generatePlan(plan.job_id, { months, cadence, language: lang }));
      setLangMode(null);
      loadSessions();
    } catch (e) {
      setError(e.message);
    } finally {
      setGenerating(false);
    }
  }

  // --- Scripts -------------------------------------------------------------
  // The "& Script" half. `itemIndex` writes the script for a calendar row (the
  // server reads the row from the stored plan, so a stale tab can't write a
  // script for an upload that was regenerated away); `brief` writes one for
  // something that was never on the calendar at all.
  async function writeScript({ itemIndex = null, brief = "", seconds }) {
    if (!plan || writingFor !== null) return;
    setWritingFor(itemIndex === null ? "brief" : itemIndex);
    setError("");
    setNotice("");
    try {
      const id = await ensureSession();
      const updated = await api.writePlanScript(id, {
        itemIndex,
        brief,
        seconds,
        language,
      });
      setPlan(updated);
      loadSessions();
      // Open the one just written. It is first in the list — the server keeps
      // scripts newest-first — so this doesn't need the id back from the call.
      const written = updated.scripts?.[0];
      if (written) setOpenScriptId(written.id);
      if (itemIndex === null) setScriptBrief("");
    } catch (e) {
      setError(e.message);
    } finally {
      setWritingFor(null);
    }
  }

  async function sendScriptToStoryboard(script) {
    if (!plan || !script) return;
    // The app keeps ONE script draft per user, so this replaces whatever is in
    // the Script to Storyboard paste box. Ask first — losing a draft you were
    // halfway through is not something to discover afterwards.
    const ok = window.confirm(
      "Load this script into Script to Storyboard?\n\n" +
        "This replaces whatever is currently in that workflow's script box."
    );
    if (!ok) return;
    setScriptBusy(true);
    setError("");
    try {
      await api.planScriptToDraft(plan.job_id, script.id);
      setOpenScriptId(null);
      if (onOpenStoryboard) onOpenStoryboard();
      else setNotice("Loaded into Script to Storyboard — open it from the sidebar.");
    } catch (e) {
      setError(e.message);
    } finally {
      setScriptBusy(false);
    }
  }

  async function downloadScript(script, format) {
    if (!plan || !script) return;
    setScriptBusy(true);
    setError("");
    try {
      await api.downloadPlanScript(plan.job_id, script.id, format);
    } catch (e) {
      setError(e.message);
    } finally {
      setScriptBusy(false);
    }
  }

  async function removeScript(script) {
    if (!plan || !script) return;
    if (!window.confirm(`Delete "${script.title}"? This can't be undone.`)) return;
    setScriptBusy(true);
    setError("");
    try {
      setPlan(await api.deletePlanScript(plan.job_id, script.id));
      setOpenScriptId(null);
      loadSessions();
    } catch (e) {
      setError(e.message);
    } finally {
      setScriptBusy(false);
    }
  }

  // Clicking a format opens the PREVIEW; the download happens from inside it.
  // Downloading straight away meant the only way to check an export was to open
  // it in Excel or Word first.
  async function confirmDownload() {
    if (!plan || !previewFormat) return;
    setExporting(previewFormat);
    setError("");
    try {
      await api.downloadPlan(plan.job_id, previewFormat);
      setPreviewFormat(null); // job done — get out of the way
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
      // Naming it is intent too — an unsaved session becomes real here.
      const id = subject.job_id || (await ensureSession());
      const updated = await api.renamePlan(id, next.trim());
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
  // Deliberately the SAME furniture as every other library: the New tile, then
  // one "Recent …" section of rows with a Filter box in its heading. A workflow
  // that invents its own gallery makes the app feel like three apps.
  //
  // ⚠ ONLY WHAT IS DIFFERENT LIVES HERE — a plan's chips and its two icon
  // buttons. The row SHAPE belongs to LibraryList.jsx.
  //
  // A plan has no picture to show, so its thumbnail is the workflow's own glyph
  // rather than an empty grey box: the column still reads as "this is the thing
  // you clicked", which is what the picture does on the other libraries.
  function renderPlanCard(s) {
    return (
      <LibraryRow
        key={s.job_id}
        onOpen={() => openSession(s.job_id)}
        openTitle="Open this plan"
        cover="🗓️"
        name={
          <div
            className="lib-title"
            onClick={() => openSession(s.job_id)}
            title={s.title}
          >
            {s.title}
          </div>
        }
        meta={
          <>
            {s.months > 0 && (
              <span className="chip">
                {s.months} month{s.months === 1 ? "" : "s"}
              </span>
            )}
            <span className="chip">
              {s.message_count} message{s.message_count === 1 ? "" : "s"}
            </span>
            {s.script_count > 0 && (
              <span className="chip">
                ✍️ {s.script_count} script{s.script_count === 1 ? "" : "s"}
              </span>
            )}
            {s.item_count > 0 && (
              <span className="chip">{s.item_count} uploads</span>
            )}
            {s.channel_title && <span className="chip">📺 {s.channel_title}</span>}
            {s.item_count === 0 && s.script_count === 0 && (
              <span className="chip">no plan yet</span>
            )}
          </>
        }
        date={formatDate(s.created_at)}
        actions={
          <>
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
              className="lib-icon danger"
              title="Delete this plan"
              onClick={() => remove(s.job_id)}
            >
              <Icon name="trash" />
            </button>
          </>
        }
      />
    );
  }

  // What the Filter box leaves standing. Matched against the two things a user
  // types looking for a plan: its name and the channel it was built for.
  const shownPlans = sessions.filter((s) =>
    matchesFilter(libQuery, s.title, s.channel_title)
  );

  if (step === "library") {
    return (
      <div className="workflow-head-wrap sb-library">
        <div className="workflow-header">
          <span className="wf-icon"><WorkflowIcon id="plan-and-script" /></span>
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

        {/* ONE section. "All Plans" used to repeat this list underneath. */}
        <LibrarySection
          title="Recent Plans"
          hint={sessions.length ? `${sessions.length} in total` : ""}
          query={libQuery}
          onQuery={setLibQuery}
          placeholder="Filter plans"
          loading={loading}
          ghosts={GHOST_ROWS}
          total={sessions.length}
          shown={shownPlans.length}
          metaLabel="Details"
          dateLabel="Created"
          emptyIcon="🗓️"
          emptyText={
            <>
              No plans yet — hit <strong>New Plan</strong> and tell the agent
              what you make.
            </>
          }
        >
          {shownPlans.map(renderPlanCard)}
        </LibrarySection>
      </div>
    );
  }

  // ---- Session ------------------------------------------------------------
  const messages = plan?.messages || [];
  // Only the NEWEST agent turn can be answered. An older question has already
  // been moved past, and offering it again would send a stale answer.
  const lastAgent =
    messages.length && messages[messages.length - 1].role === "agent"
      ? messages[messages.length - 1]
      : null;
  const liveQuestions =
    lastAgent && lastAgent.at !== dismissedAt ? lastAgent.questions || [] : [];
  // Declared here rather than with the other derived values because it needs
  // `messages`. A half-filled Custom box must not reach the server as a silent
  // default — better a disabled button than a plan for the wrong span.
  const canGenerate = messages.length > 0 && months >= 1 && Boolean(cadence);
  const built = plan?.plan || {};
  const items = built.items || [];
  const channel = plan?.channel || {};
  const scripts = plan?.scripts || [];
  // The session's running token total — every chat turn, every calendar, every
  // script, retries included. Summed server-side from what actually happened.
  const sessionUsage = plan?.usage || {};
  const openScript = scripts.find((s) => s.id === openScriptId) || null;

  return (
    <div className="workflow-head-wrap plan-page">
      <div className="workflow-header">
        {/* Back leads the header row, in the same box as the icon beside it —
            see `.wf-back` in shell.css. */}
        <button
          type="button"
          className="btn back-btn wf-back"
          onClick={() => { setStep("library"); loadSessions(); }}
          title="Your plans"
          aria-label="Your plans"
        >
          ←
        </button>
        <span className="wf-icon"><WorkflowIcon id="plan-and-script" /></span>
        <div>
          <h1 className="wf-title">{plan?.title || "Plan & Script"}</h1>
          <p className="muted">
            {items.length > 0
              ? [
                  `${items.length} uploads`,
                  `${built.months} month${built.months === 1 ? "" : "s"}`,
                  built.cadence,
                  // Shown so it's obvious what the board was written in, and
                  // that regenerating in another language is possible.
                  built.language ? languageLabel(built.language) : "",
                ]
                  .filter(Boolean)
                  .join(" · ")
              : "Tell the agent what you make and who it's for."}
          </p>
        </div>
      </div>

      <div className="review-actions top-actions">
        <div className="review-actions-right">
          {/* What this session has spent, where it can be seen while spending
              more. Nothing else in the app showed text-token cost at all, so
              a long conversation used to be entirely invisible. */}
          {sessionUsage.total > 0 && (
            <span className="tiny muted plan-usage" title="Tokens used by this planning session — chat, calendars and scripts, including retries. Cost is an estimate; only Google bills.">
              {usageLine(sessionUsage)}
            </span>
          )}
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

      {/* Write a script — the "& Script" half.
          Its own box rather than only a button on the calendar, because plenty
          of scripts are never on a calendar: a one-off, a client brief, an idea
          that arrived this morning. The planner's conversation is still used as
          background, so a script asked for here knows who the audience is. */}
      <section className="card plan-script-ask">
        <h2>Write a script</h2>
        <p className="muted tiny">
          For anything not on the calendar. Everything said above is used as
          background — who you make for, and how you sound.
          {items.length > 0 &&
            " For a planned upload, use the button on its card instead."}
        </p>
        <div className="plan-script-row">
          <label className="field plan-script-brief">
            <span className="field-label">What is the video?</span>
            <input
              value={scriptBrief}
              placeholder="e.g. a 3-minute horror short about a lift that stops on floor 7"
              maxLength={4000}
              onChange={(e) => setScriptBrief(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && scriptBrief.trim()) {
                  writeScript({ brief: scriptBrief.trim(), seconds: scriptSeconds });
                }
              }}
            />
          </label>
          <label className="field">
            <span className="field-label">Length</span>
            <select
              value={String(scriptSeconds)}
              onChange={(e) => setScriptSeconds(Number(e.target.value))}
            >
              {SCRIPT_LENGTHS.map(([value, label]) => (
                <option key={value} value={String(value)}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <div className="field plan-actions-field">
            <span className="field-label" aria-hidden="true">
              &nbsp;
            </span>
            <button
              className="btn primary"
              disabled={writingFor !== null || !scriptBrief.trim()}
              onClick={() =>
                writeScript({ brief: scriptBrief.trim(), seconds: scriptSeconds })
              }
              title={
                !scriptBrief.trim() ? "Describe the video first" : undefined
              }
            >
              {writingFor === "brief" ? (
                <>
                  <span className="spinner-inline" /> Writing in{" "}
                  {languageLabel(language)}…
                </>
              ) : (
                <>
                  <Icon name="text" /> Write script
                </>
              )}
            </button>
          </div>
        </div>
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

        {/* The agent's questions as clickable answers, directly above the box
            you'd otherwise type into. Dismissable — it's a shortcut, never a
            gate; you can always answer in your own words instead. */}
        {!sending && (
          <PlanQuestions
            questions={liveQuestions}
            busy={sending}
            onDismiss={() => setDismissedAt(lastAgent?.at || null)}
            onSubmit={submitAnswers}
          />
        )}

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

          {/* The language sits in the row with the other choices, not only
              inside the popup — otherwise the plan comes back in Hinglish and
              nothing on the page says so. Clicking it reopens the picker. */}
          <label className="field">
            <span className="field-label">Language</span>
            <button
              type="button"
              className="btn plan-lang-btn"
              onClick={() => setLangMode("pick")}
              title="Change the language the plan is written in"
            >
              <span className="plan-lang-name">{languageLabel(language)}</span>
              <span className="plan-lang-caret" aria-hidden="true">▾</span>
            </button>
          </label>

          {/* The buttons sit in the SAME column structure as the selects — an
              invisible label on top, control underneath — so they land in the
              identical band instead of being aligned across two different
              structures and ending up a few pixels out. */}
          <div className="field plan-actions-field">
          <span className="field-label" aria-hidden="true">
            &nbsp;
          </span>
          <div className="plan-actions">
          <button
            className="btn primary"
            onClick={() => setLangMode("generate")}
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
              // Say WHAT it's writing, not just that it's busy — a 36-item plan
              // takes a while and "Planning…" alone left the user unsure the
              // language had even been applied.
              <>
                <span className="spinner-inline" /> Writing in {languageLabel(language)}…
              </>
            ) : items.length ? (
              "Regenerate plan"
            ) : (
              "Generate plan"
            )}
          </button>

          {/* Exports sit in the SAME row as Generate, so everything you do to
              the calendar is in one place instead of a stray row further down
              the page. They only appear once there is a plan to export. */}
          {items.length > 0 && (
            <>
              <span className="plan-row-sep" aria-hidden="true" />
              {["xlsx", "docx", "csv"].map((f) => (
                <button
                  key={f}
                  className="btn"
                  onClick={() => {
                    setPreviewSheet("Calendar"); // always open on the calendar
                    setPreviewFormat(f);
                  }}
                  title={`Preview the ${f.toUpperCase()} before downloading`}
                >
                  <Icon name="download" /> {f.toUpperCase()}
                </button>
              ))}
            </>
          )}
          </div>
          </div>
        </div>
      </section>

      <PlanLanguageModal
        open={Boolean(langMode)}
        // The language CURRENTLY selected in the row — not the one the last
        // plan happened to be built in. Seeding it from the old plan meant
        // picking Hinglish in the row, hitting Regenerate, and the popup
        // quietly reopening on English, so the plan came back English.
        initial={language}
        mode={langMode}
        busy={generating}
        onClose={() => setLangMode(null)}
        onConfirm={(lang) => {
          setLanguage(lang);
          if (langMode === "generate") generate(lang);
          else setLangMode(null);
        }}
      />

      {/* Mounted outside the calendar block so it survives a regenerate. */}
      <PlanExportPreview
        format={previewFormat}
        plan={built}
        title={plan?.title}
        sheet={previewSheet}
        onSheet={setPreviewSheet}
        onClose={() => setPreviewFormat(null)}
        onDownload={confirmDownload}
        downloading={Boolean(exporting)}
      />

      {/* Every script written in this session. Newest first, same order the
          server keeps them in. */}
      {scripts.length > 0 && (
        <section className="card plan-scripts">
          <div className="plan-strategy-head">
            <h2>
              Scripts <span className="muted">({scripts.length})</span>
            </h2>
          </div>
          <div className="plan-script-list">
            {scripts.map((s) => (
              <button
                key={s.id}
                type="button"
                className="plan-script-card"
                onClick={() => setOpenScriptId(s.id)}
                title="Open this script"
              >
                <span className="plan-script-title">{s.title}</span>
                <span className="plan-script-meta">
                  {[
                    s.item_slot,
                    `${s.scenes?.length || 0} scene${s.scenes?.length === 1 ? "" : "s"}`,
                    s.estimated_seconds ? `~${formatRuntime(s.estimated_seconds)}` : "",
                    s.rating,
                    languageLabel(s.language),
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
                {/* Per-script cost, on the card. The session total in the
                    header is the sum of these, so the two can be checked
                    against each other. */}
                {s.usage?.total > 0 && (
                  <span className="plan-script-tokens tiny muted">
                    {usageLine(s.usage)}
                  </span>
                )}
              </button>
            ))}
          </div>
        </section>
      )}

      <PlanScriptModal
        script={openScript}
        busy={scriptBusy}
        onClose={() => setOpenScriptId(null)}
        onDownload={(format) => downloadScript(openScript, format)}
        onSendToStoryboard={() => sendScriptToStoryboard(openScript)}
        onDelete={() => removeScript(openScript)}
      />

      {/* The calendar */}
      {items.length > 0 && (
        <>
          {built.summary && (
            <section className="card">
              <div className="plan-strategy-head">
                <h2>Strategy</h2>
                {/* States plainly what this calendar is written in — the whole
                    board below is in that language. */}
                {built.language && (
                  <span className="plan-chip plan-lang-chip">
                    ✍️ {languageLabel(built.language)}
                  </span>
                )}
                {/* What THIS calendar cost, not the session — so the price of a
                    regenerate is visible as its own number. */}
                {built.usage?.total > 0 && (
                  <span className="tiny muted plan-usage">{usageLine(built.usage)}</span>
                )}
              </div>
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

          <div className="plan-grid">
            {items.map((it, i) => {
              // The scripts already written for this row. Keyed on the index,
              // which is what the server stored — a plan that has been
              // regenerated since will simply show none, which is right: those
              // scripts belong to the calendar that produced them.
              const written = scripts.filter((s) => s.item_index === i);
              const runtime = secondsFromFormat(it.format);
              return (
              <article key={i} className="card plan-item">
                <header className="plan-item-head">
                  <span className="plan-slot">{it.slot}</span>
                  {it.goal && (
                    <span className={`plan-chip ${GOAL_TONE[it.goal] || ""}`}>{it.goal}</span>
                  )}
                  {it.effort && <span className="plan-chip">{it.effort} effort</span>}
                </header>
                <h3 className="plan-item-title">{it.title}</h3>
                {/* Every card is the same size; the detail scrolls inside when
                    there's more of it. The slot, chips and title stay pinned so
                    a scrolled card is still identifiable at a glance. */}
                <div className="plan-item-body">
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
                </div>

                {/* The card's own foot, pinned below the scrolling detail — so
                    "write the script for this one" is on the thing it writes
                    the script for, rather than in a separate list you have to
                    match up by title. */}
                <footer className="plan-item-foot">
                  <button
                    className="btn small primary"
                    disabled={writingFor !== null}
                    onClick={() => writeScript({ itemIndex: i, seconds: runtime })}
                    // The length is read off this row's own `format` — that
                    // field is what says how long the video is — and the button
                    // says which length it will use rather than deciding
                    // silently.
                    title={`Write a ${formatRuntime(runtime)} script for this upload`}
                  >
                    {writingFor === i ? (
                      <>
                        <span className="spinner-inline" /> Writing…
                      </>
                    ) : (
                      <>
                        <Icon name="text" />{" "}
                        {written.length ? "Write another" : "Write script"} ·{" "}
                        {formatRuntime(runtime)}
                      </>
                    )}
                  </button>
                  {written.map((s) => (
                    <button
                      key={s.id}
                      className="btn small ghost"
                      onClick={() => setOpenScriptId(s.id)}
                      title={`Open "${s.title}"`}
                    >
                      <Icon name="eye" /> {s.scenes?.length || 0} scenes
                    </button>
                  ))}
                </footer>
              </article>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
