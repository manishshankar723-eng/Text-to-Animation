// EditorChat.jsx — the ✨ AI Editor panel: a conversation that edits the timeline.
//
// ---------------------------------------------------------------------------
// ⚠ IT WEARS `sc-*`, THE SAME CLASSES AS ScriptChat AND BoardAssistant.
// ---------------------------------------------------------------------------
// Three chats in one product that look like three different products is three
// products. The log, the bubbles and the composer are the ones this app already
// has; what is new here — and only what is new — gets `ec-*`: the dock chrome,
// the question block and the plan preview. BoardAssistant states the same rule
// at its top and for the same reason.
//
// ---------------------------------------------------------------------------
// ⚠ THREE DOCKS, AND THE ADMIN PANEL CHOOSES. Asked for outright: *"tu dono kar
// do mai admin panel se change kar lunga"*.
// ---------------------------------------------------------------------------
//   `right`    a column beside the timeline — what Descript's Underlord and
//              Premiere's AI Assistant both do, and the one with room to read a
//              plan without scrolling.
//   `sidebar`  slides straight out of the ✨ button in the rail, over the editor.
//   `float`    a small window dragged wherever the person wants it.
//   `user`     all of them are offered and the CHOICE is remembered in this
//              browser.
//
// The dock is a class on the root and nothing else — no second render path, no
// duplicated markup. A layout that forked into three component trees would be
// three panels to fix every bug in.
//
// ---------------------------------------------------------------------------
// ⚠ IT MOVES AND IT RESIZES, AND THOSE ARE TWO DIFFERENT THINGS.
// ---------------------------------------------------------------------------
// Asked for outright: *"chat bot ka popup screen ko move kar sake and chhota aur
// bara kar sake"*. **Every dock resizes** — the two pinned ones by their inner
// edge (width only; they are full height by definition), the floating one by its
// bottom-right corner. **Only the floating one moves**, because the other two
// are pinned to a screen edge and "somewhere else" has no meaning for them.
//
// ⚠ THE EDGE HANDLE IS `PaneSplitter`, THE ONE THE WORKSPACE SEAMS ALREADY USE
// — same drag maths, same double-click-to-reset, same arrow keys, same 2px line
// that only appears on hover. A second resize handle written here would have
// been a second thing that behaves almost like the seams. The corner grip is the
// only new one, and it is new because no seam in this app drags on both axes.
//
// ⚠ AND THE NUMBERS ARE CLAMPED IN `panel_box.js`, NOT HERE. See its header for
// why the window is kept WHOLLY on screen rather than "mostly" — there is no
// taskbar to fetch it back from once its Send button is past the edge.
//
// ---------------------------------------------------------------------------
// ⚠ HOW SEE-THROUGH IT IS BELONGS TO THE ADMIN, NOT TO THE PERSON DRAGGING IT.
// ---------------------------------------------------------------------------
// *"admin panel mai ai editor se chatbot panel ko transparent kar sake"*. It
// arrives on the config as a PERCENTAGE and becomes one custom property,
// `--ec-opacity`; `editor-chat.css` does the rest. `blur` is its partner and
// works the same way (`--ec-blur`, in px) — how far the film underneath is
// softened so the conversation stays readable on top of it. It is not a user control on
// purpose: a customer who fades their own chat until they cannot read it has no
// way back.
//
// ⚠ AND IT RUNS 0–100 WITH NO FLOOR. There was one, at 40, and it was picked off
// the DARK theme — the light theme is a white panel over a near-white page, so
// 40 there is a difference nobody can see (*"dark mai thora ho raha hai white mai
// to ho hi nhi raha hai"*). The operator judges it against their own deployment:
// *"0 to 100 rakho mai admin panel se check kar lunga kitna better hai"*.
//
// ⚠ AND THE BLUR IS A SLIDER FOR THE SAME REASON, NOT A NUMBER I PICKED. It was
// hard-coded at 16px (unreadable in light), then removed (unreadable at low
// opacity) — one number chosen for every screen, wrong twice. *"text padhne mai
// mushkil hai isliye blur daalo — tum nhi, admin panel pe daal do, mai set kar
// lunga blur ko v"*. It ships at 0, which is exactly what is on screen today.
//
// ---------------------------------------------------------------------------
// ⚠ IT PLANS, THE USER APPLIES. NOTHING ON THIS PANEL EDITS ON ARRIVAL.
// ---------------------------------------------------------------------------
// A plan comes back as a table with a count under it and an Apply button. That
// is the same contract `BoardAssistant` has ("⚠ IT PLANS, THE USER APPLIES") and
// the same one the Director's preview has. One typed sentence must never be able
// to rearrange somebody's film behind their back — and once it has been applied,
// Undo is on the bubble that did it.
//
// ⚠ THE PANEL OWNS NO LOGIC. Everything it draws comes off `useEditorChat` —
// the same relationship `DirectorPanel` has to `useDirectorRun`: a component
// that renders a decision, not one that makes it.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import PaneSplitter from "./PaneSplitter.jsx";
import { describeStep } from "../animatic/agent/actions.js";
// ⚠ THE GEOMETRY IS A MODULE, NOT FOUR NUMBERS IN A HANDLER. See its header.
import {
  NARROW_W,
  clampBox,
  clampWidth,
  defaultBox,
  forgetBox,
  readBox,
  readWidth,
  viewport,
  writeBox,
  writeWidth,
} from "../animatic/agent/panel_box.js";
// ⚠ THE LABELS COME FROM THE SAME MODULE THAT READS THE OFFER, so a door added
// there cannot arrive here as a blank button with no name on it.
import { DOOR_LABEL } from "../animatic/agent/chat_turn.js";
import { capabilities } from "../animatic/agent/capabilities.js";

/** Remembered only when the operator picked "let each person choose". */
const DOCK_KEY = "aniwala.editorChatDock.v1";

const DOCKS = [
  { id: "right", label: "Dock right", ico: "▥" },
  { id: "sidebar", label: "Beside the rail", ico: "▤" },
  // ⚠ THE ONE THAT MOVES. Its glyph is a window, not a column, because that is
  // the difference a person is picking between.
  { id: "float", label: "Floating window — drag it anywhere", ico: "❐" },
];

/** ⚠ CHECKED AGAINST THE LIST, NOT AGAINST A HAND-WRITTEN `||` CHAIN — a dock
 *  added above and forgotten here comes back from storage as "right" forever. */
const DOCK_IDS = DOCKS.map((d) => d.id);

function storedDock() {
  try {
    const value = localStorage.getItem(DOCK_KEY);
    return DOCK_IDS.includes(value) ? value : "right";
  } catch {
    return "right";
  }
}

/**
 * The empty state. ⚠ IT SAYS WHAT TO TYPE, NOT WHAT THE FEATURE IS.
 *
 * A blank chat with "Hi, I'm your AI assistant!" in it teaches nobody anything.
 * Three real sentences do, and they are deliberately the three SHAPES of thing
 * this can do — a question, an edit, and a vague ask that will come back as a
 * question with options, which is the behaviour most worth discovering early.
 */
const EXAMPLES = [
  "How long is this film?",
  "Put a dissolve on every scene change",
  "Make the opening feel more urgent",
];

/** `add_transition` → "Transitions". The preview groups by what a step DOES. */
const FAMILY = {
  add_transition: "Transitions",
  set_transition_duration: "Transitions",
  remove_transition: "Transitions",
  add_effect: "Effects",
  set_effect_param: "Effects",
  remove_effect: "Effects",
  add_text: "Text",
  set_text: "Text",
  apply_text_preset: "Text",
  remove_text: "Text",
  add_shape: "Shapes",
  set_shape: "Shapes",
  remove_shape: "Shapes",
  set_shot_duration: "Timing",
  set_all_durations: "Timing",
  push_in: "Camera",
  add_shot_motion: "Camera",
  clear_shot_motion: "Camera",
  set_shot_transform: "Camera",
  add_layer: "Audio",
  set_track_fade: "Audio",
  set_track_volume: "Audio",
  add_crossfade: "Audio",
  note: "Notes",
};

export default function EditorChat({
  open,
  onClose,
  chat,
  readCtx,
  // "right" | "sidebar" | "float" | "user" — the operator's setting, from
  // /editor-chat/config.
  dock = "right",
  // How solid the panel is drawn, 0–100. The operator's, not the customer's.
  opacity = 100,
  // How far the film underneath is blurred, 0–40 px. The operator's too, and it
  // only does anything while `opacity` is below 100.
  blur = 0,
  greeting = "",
}) {
  const [draft, setDraft] = useState("");
  const [mine, setMine] = useState(storedDock);
  const logRef = useRef(null);
  const inputRef = useRef(null);
  const panelRef = useRef(null);

  // ⚠ THE SETTING WINS UNLESS IT SAYS OTHERWISE. `user` is the only value that
  // hands the choice over; the other three are the operator's decision and a
  // remembered preference must not survive them changing it.
  const side = dock === "user" ? mine : dock;

  function pickDock(next) {
    setMine(next);
    try {
      localStorage.setItem(DOCK_KEY, next);
    } catch {
      // Storage blocked. The choice still applies for this page load.
    }
  }

  // ======================================================== size and position
  // ⚠ THE VIEWPORT IS STATE, NOT A READ AT DRAW TIME. A window the browser has
  // been shrunk under has to be pulled back in, and nothing else re-renders this
  // panel when that happens — the timeline behind it does, and it is not this
  // panel's parent.
  const [vp, setVp] = useState(viewport);
  useEffect(() => {
    const onResize = () => setVp(viewport());
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // ⚠ UNDER 820px NONE OF THIS APPLIES. Both docks become one full-width sheet
  // there and a floating window has nothing to float over — and an inline style
  // beats the media query that says so, so the panel has to withhold the style
  // rather than trust the stylesheet to override it.
  const narrow = vp.w <= NARROW_W;
  const floating = side === "float" && !narrow;

  const [box, setBox] = useState(readBox);
  // The drag handlers need the CURRENT box on every pointer move, and a state
  // value closed over at pointerdown is the box as it was when the drag began.
  const boxRef = useRef(box);
  const putBox = useCallback((next) => {
    boxRef.current = next;
    setBox(next);
  }, []);

  // Pull it back on screen whenever the browser window changes shape.
  useEffect(() => {
    putBox(clampBox(boxRef.current, vp));
  }, [vp, putBox]);

  // The docked column's width. ⚠ `null` MEANS "NOBODY HAS DRAGGED IT" and the
  // CSS `clamp(320px, 26vw, 420px)` is still in charge; the measure below turns
  // that into a number for the handle to report, WITHOUT marking it as a choice
  // this browser has made.
  const [width, setWidth] = useState(readWidth);
  const touched = useRef(width !== null);

  useEffect(() => {
    if (!open || narrow || floating || width !== null) return;
    const el = panelRef.current;
    if (el) setWidth(clampWidth(el.getBoundingClientRect().width, vp));
  }, [open, narrow, floating, width, vp]);

  // ⚠ WRITTEN AFTER THE DRAG SETTLES, NOT ON EVERY POINTER MOVE. `PaneSplitter`
  // reports continuously — sixty `localStorage` writes a second is what that
  // would be without this, on the main thread, for one drag.
  useEffect(() => {
    if (!touched.current || width === null) return;
    const t = setTimeout(() => writeWidth(width), 250);
    return () => clearTimeout(t);
  }, [width]);

  const [dragging, setDragging] = useState(false);

  /** One drag, either kind: read the start once, clamp every move, save on up. */
  const drag = useCallback(
    (e, onMove) => {
      if (e.button !== 0) return;
      e.preventDefault();
      const from = { x: e.clientX, y: e.clientY, box: boxRef.current };
      setDragging(true);
      const move = (ev) =>
        putBox(clampBox(onMove(from, ev.clientX - from.x, ev.clientY - from.y), viewport()));
      const up = () => {
        setDragging(false);
        // On the WINDOW, like `PaneSplitter`: the pointer leaves the handle
        // within a few pixels and the drag has to keep working when it does.
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        writeBox(boxRef.current);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    },
    [putBox]
  );

  /** The title bar moves the window — unless the press landed on a button. */
  function beginMove(e) {
    if (!floating) return;
    // ⚠ THE ✕ AND THE DOCK SWITCH LIVE IN THIS BAR. Without this a press on
    // Close starts a drag, and the click that would have shut the panel is
    // swallowed by `preventDefault`.
    if (e.target.closest("button")) return;
    drag(e, (from, dx, dy) => ({ ...from.box, x: from.box.x + dx, y: from.box.y + dy }));
  }

  /** The corner grip resizes both axes at once. No seam in this app does that. */
  function beginResize(e) {
    drag(e, (from, dx, dy) => ({ ...from.box, w: from.box.w + dx, h: from.box.h + dy }));
  }

  function resetBox() {
    const next = defaultBox(vp);
    putBox(next);
    forgetBox();
  }

  /** Arrow keys on the grip, so the window is resizable without a pointer. */
  function nudge(e) {
    const step = 24;
    const now = boxRef.current;
    let next = null;
    if (e.key === "ArrowLeft") next = { ...now, w: now.w - step };
    else if (e.key === "ArrowRight") next = { ...now, w: now.w + step };
    else if (e.key === "ArrowUp") next = { ...now, h: now.h - step };
    else if (e.key === "ArrowDown") next = { ...now, h: now.h + step };
    else if (e.key === "Home") {
      resetBox();
      e.preventDefault();
      return;
    } else return;
    e.preventDefault();
    const clamped = clampBox(next, vp);
    putBox(clamped);
    writeBox(clamped);
  }

  // Keep the newest turn in view — a log that has to be scrolled to read the
  // answer reads as a log that did not answer.
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat.turns, chat.sending, chat.running]);

  // ⚠ FOCUS ON OPEN, NOT ON EVERY RENDER. The panel is opened to type in.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  if (!open) return null;

  const busy = chat.sending || chat.running;

  function submit() {
    const text = draft.trim();
    if (!text || busy || chat.blocked) return;
    setDraft("");
    chat.send(text);
  }

  // ⚠ ONE STYLE OBJECT, AND MOST OF IT IS EMPTY MOST OF THE TIME. The floating
  // window is placed here because only JavaScript knows where the person left
  // it; everything else — the edges, the shadow, the radius — is the stylesheet's.
  const style = { "--ec-opacity": opacity, "--ec-blur": blur };
  if (floating) {
    style.left = box.x;
    style.top = box.y;
    style.width = box.w;
    style.height = box.h;
    // `.ec-panel` pins top AND bottom to 0. A floating window sets its own
    // height, so the pin at the far end has to be released or the height is
    // ignored and the panel is full-screen-tall wherever it was dropped.
    style.right = "auto";
    style.bottom = "auto";
  } else if (!narrow && width !== null && touched.current) {
    // ⚠ ONLY ONCE SOMEBODY HAS ACTUALLY DRAGGED IT. `width` is also set by the
    // measure above, purely so the handle has a number to report — and writing
    // THAT back as an inline width would freeze every untouched panel at
    // whatever `clamp(320px, 26vw, 420px)` happened to be on the day they opened
    // it, so it would stop following the window for people who never resized.
    style.width = width;
  }

  return (
    <aside
      ref={panelRef}
      className={
        `ec-panel ec-dock-${side}` +
        (opacity < 100 ? " is-see-through" : "") +
        // ⚠ BOTH CONDITIONS, AND THE CLASS IS THE WHOLE POINT — `backdrop-filter`
        // at any value, `0px` included, makes the browser re-filter everything
        // behind this panel every frame over a playing timeline. It is only worth
        // that when there is something showing through AND somebody asked for it.
        (opacity < 100 && blur > 0 ? " is-blurred" : "") +
        (dragging ? " is-dragging" : "")
      }
      style={style}
      aria-label="AI Editor"
      // ⚠ NOT `role="dialog"`. It is a panel that sits beside the work, not a
      // modal over it — the timeline stays live and keyboard-reachable while the
      // chat is open, which is the whole point of docking it.
    >
      {/* ⚠ THE WIDTH HANDLE IS THE WORKSPACE'S OWN SEAM, not a new widget — see
          the note at the top of this file. It sits on the edge that faces the
          EDITOR, so the drag that widens the panel is the one that pushes into
          the work: on the right-hand dock that is the left edge and the pointer
          travelling right makes it narrower (`sign={-1}`); out of the rail it is
          the mirror image. Never drawn on the floating window, which has a
          corner instead, and never under 820px, where the panel is the width of
          the screen and there is nothing to drag. */}
      {!floating && !narrow && width !== null && (
        <PaneSplitter
          orientation="vertical"
          className={`ec-side-split ec-side-split-${side}`}
          value={width}
          min={300}
          max={clampWidth(9999, vp)}
          sign={side === "sidebar" ? 1 : -1}
          label="Panel width"
          onChange={(next) => {
            touched.current = true;
            setWidth(clampWidth(next, vp));
          }}
          onReset={() => {
            touched.current = true;
            setWidth(clampWidth(380, vp));
          }}
        />
      )}

      <header
        className="ec-head"
        onPointerDown={beginMove}
        onDoubleClick={floating ? resetBox : undefined}
        title={
          floating
            ? "Drag to move the panel · double-click to put it back"
            : undefined
        }
      >
        <span className="ec-head-title">
          <span aria-hidden="true">✨</span> AI Editor
        </span>

        {/* Only when the operator has handed the choice over. Two buttons that
            do nothing on a locked deployment would be two buttons to explain. */}
        {dock === "user" && (
          <span className="ec-dock-pick" role="group" aria-label="Where this panel sits">
            {DOCKS.map((d) => (
              <button
                key={d.id}
                type="button"
                className={`ec-dock-btn ${side === d.id ? "on" : ""}`}
                title={d.label}
                aria-label={d.label}
                aria-pressed={side === d.id}
                onClick={() => pickDock(d.id)}
              >
                {d.ico}
              </button>
            ))}
          </span>
        )}

        <button
          type="button"
          className="modal-close"
          onClick={onClose}
          title="Close the AI Editor"
          aria-label="Close the AI Editor"
        >
          ✕
        </button>
      </header>

      <div className="sc-chat-log ec-log" ref={logRef}>
        {chat.turns.length === 0 ? (
          <div className="ec-empty">
            <p className="muted">
              {greeting ||
                "Tell me what you want changed and I'll show you the edit before " +
                  "anything happens. If I'm not sure what you mean, I'll ask."}
            </p>
            <div className="ec-examples">
              {EXAMPLES.map((line) => (
                <button
                  key={line}
                  type="button"
                  className="ec-example"
                  onClick={() => chat.send(line)}
                  disabled={busy || Boolean(chat.blocked)}
                >
                  {line}
                </button>
              ))}
            </div>
          </div>
        ) : (
          chat.turns.map((turn) => (
            <Turn
              key={turn.id}
              turn={turn}
              chat={chat}
              readCtx={readCtx}
              busy={busy}
            />
          ))
        )}

        {chat.sending && (
          <div className="sc-msg is-agent">
            <div className="sc-msg-text muted">
              {/* ⚠ "LOOKING AT…" RATHER THAN "THINKING…" WHILE IT IS LOOKING, and
                  it carries the model's OWN reason. A look is the slowest turn
                  this panel has — a dozen pictures fetched, uploaded and read —
                  and a spinner that says "Thinking" through all of it is the
                  shape of wait people report as a hang. Saying what it is doing,
                  and why, is the difference between a pause and a fault. */}
              <span className="spinner-inline" />{" "}
              {chat.looking || "Thinking…"}
              {/* ⚠ THE SECOND HAND. A spinner says "something is happening"; only
                  a number says "and it is still happening". Without it a healthy
                  40s turn and a wedged one look identical, which is what made
                  the same message get sent three times. Held back for five
                  seconds because a counter on every quick answer is noise. */}
              {chat.elapsed > 5 && <span className="ec-elapsed"> {chat.elapsed}s</span>}
            </div>
            {/* ⚠ IT STOPS THE WAIT, NOT THE SPEND — and the line it writes when
                pressed says so, because a Stop button most people would read as
                "cancel the charge" has to correct that itself. Only offered once
                the wait is long enough to be worth escaping. */}
            {chat.elapsed > 10 && (
              <button
                type="button"
                className="btn ghost small ec-stop"
                onClick={chat.stop}
                title="Stop waiting for this reply. The AI was already asked, so this turn still counts."
              >
                Stop waiting
              </button>
            )}
          </div>
        )}
      </div>

      {chat.error && <div className="error sc-chat-error">{chat.error}</div>}
      {chat.blocked && <div className="ec-blocked">{chat.blocked}</div>}

      <div className={`sc-composer ec-composer ${busy ? "is-busy" : ""}`}>
        <textarea
          ref={inputRef}
          className="sc-composer-input"
          rows={2}
          value={draft}
          disabled={busy || Boolean(chat.blocked)}
          placeholder="Add music, cut the slow bit, put a title on shot 3…"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends, Shift+Enter is a newline — the same rule as every
            // other chat box in this app, and what people are trained on.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <div className="sc-composer-foot">
          <span className="tiny muted sc-composer-hint">
            {/* ⚠ IT USED TO SAY "nothing changes until you press Apply" ON EVERY
                TURN, INCLUDING AN EMPTY CHAT — a reassurance that points at a
                button which is not on screen, and which most turns never grow.
                Caught by `editor_chat_render_check.py`, which asserted that an
                answer draws no Apply and found the word down here. The promise
                is the same and it is now true whatever is on screen. */}
            {chat.running
              ? "Making the edit…"
              : chat.sending
                ? `Thinking… ${chat.elapsed}s`
                : "Enter to send · I'll show you any edit before it happens"}
          </span>
          <button
            type="button"
            className="btn primary small"
            onClick={submit}
            disabled={busy || !draft.trim() || Boolean(chat.blocked)}
          >
            {busy ? (
              <>
                <span className="spinner-inline" /> Working…
              </>
            ) : (
              "Send"
            )}
          </button>
        </div>
      </div>

      <div className="sc-chat-foot ec-foot">
        {chat.turns.length > 0 && (
          <button type="button" className="btn ghost small" onClick={chat.clear}>
            Clear chat
          </button>
        )}
        <span className="tiny muted">
          {/* ⚠ THE ALLOWANCE IS SHOWN BEFORE IT RUNS OUT, not at the refusal. A
              quota you discover by being blocked is a bad surprise; one you can
              see is a budget. Hidden entirely when it is unlimited — a number
              with no ceiling is noise. */}
          {chat.quota.limit !== null
            ? `${chat.quota.used} of ${chat.quota.limit} messages this month · `
            : ""}
          saved in this browser only
        </span>
      </div>

      {/* ⚠ THE CORNER, AND IT IS THE ONE HANDLE IN THIS APP THAT DRAGS ON BOTH
          AXES — which is why it is not a `PaneSplitter`. It carries the seam's
          other two habits anyway: double-click puts the window back where it
          opens, and the arrow keys resize it for anyone who cannot drag. */}
      {floating && (
        <div
          className="ec-grip"
          onPointerDown={beginResize}
          onDoubleClick={resetBox}
          onKeyDown={nudge}
          role="separator"
          tabIndex={0}
          aria-label="Panel size"
          title="Drag to resize · double-click to put it back"
        />
      )}
    </aside>
  );
}

/** One turn in the log. A person's line, or one of the assistant's three kinds. */
function Turn({ turn, chat, readCtx, busy }) {
  if (turn.role === "user") {
    return (
      <div className="sc-msg is-user">
        <div className="sc-msg-text">{turn.text}</div>
      </div>
    );
  }

  return (
    <div className="sc-msg is-agent">
      {turn.text && <div className="sc-msg-text">{turn.text}</div>}
      {turn.kind === "ask" && turn.ask && (
        <Ask turn={turn} chat={chat} busy={busy} />
      )}
      {turn.kind === "plan" && (
        <Plan turn={turn} chat={chat} readCtx={readCtx} busy={busy} />
      )}
      {(turn.passes || []).length > 0 && <Offers turn={turn} chat={chat} />}
      {(turn.drops || []).length > 0 && <Drops drops={turn.drops} />}
    </div>
  );
}

/**
 * PAID WORK THE CHAT IS OFFERING — one button per door.
 *
 * ⚠ **THE BUTTON DOES NOT SPEND AND DOES NOT SAY A PRICE.** It opens the same
 * dialog ✨ Animate, 🎙 Voiceover and 🖼 Animatic images already open, and that
 * dialog is what asks the server for the cost and what refuses an account whose
 * plan does not cover it. Both of those jobs live in ONE place in this app, and
 * a figure printed here — computed on this side, from the board the browser is
 * holding — would be a second answer about somebody's money sitting right next
 * to the one that charges. So the label says what it WOULD do and where it goes.
 *
 * ⚠ **AND IT SAYS SO OUT LOUD**, because a button beside a paid thing is read as
 * a button that buys it. "Opens the price first" is the whole promise, and it is
 * on screen rather than in a tooltip nobody hovers.
 *
 * ⚠ **NOT DISABLED WHILE A PLAN IS RUNNING.** These are separate doors — the
 * steps are landing on the timeline and reading the price of a voiceover does
 * not touch them — and a button greyed out for a reason the user cannot see is
 * the thing they report as broken.
 */
function Offers({ turn, chat }) {
  return (
    <div className="ec-offers">
      {turn.passes.map((offer) => {
        const door = DOOR_LABEL[offer.door] || {};
        return (
          <div className="ec-offer" key={offer.door}>
            <div className="ec-offer-text">
              <strong>
                {door.glyph} {door.label}
                {offer.shot ? ` — shot ${offer.shot}` : ""}
              </strong>
              {/* The model's own sentence about THIS film, or the door's own
                  one-liner when it did not write one. */}
              <span className="tiny muted">{offer.why || door.note || ""}</span>
            </div>
            <button
              type="button"
              className="btn small btn-row"
              onClick={() => chat.openPass(offer.door, offer.shot)}
            >
              See the price
            </button>
          </div>
        );
      })}
      <p className="tiny muted ec-offer-foot">
        This costs money. Nothing is charged until you read the price and press the
        button there.
      </p>
    </div>
  );
}

/**
 * ⭐ THE QUESTION, WITH OPTIONS — the reply kind this whole feature exists for.
 *
 * ⚠ THE LAST LINE IS THE POINT. Every competitor in this category guesses and
 * acts; asked for outright: *"if it unsure about anything give us the options and
 * ask if not these then what"*. So a closed row of chips would be a form, and
 * "None of these — tell me what you want" is what makes it a conversation. It is
 * not a fourth option: it points at the composer, which is where the answer that
 * was not on the list gets typed.
 *
 * ⚠ AND AN ANSWERED QUESTION STOPS BEING CLICKABLE. An old question answered a
 * second time three messages later is a second, different film being asked for
 * against a timeline that has moved on.
 */
function Ask({ turn, chat, busy }) {
  const answered = Boolean(turn.chosen);
  return (
    <div className={`ec-ask ${answered ? "is-answered" : ""}`}>
      <p className="ec-ask-q">{turn.ask.question}</p>
      <div className="ec-ask-options">
        {(turn.ask.options || []).map((option) => (
          <button
            key={option.id}
            type="button"
            className={`ec-option ${turn.chosen === option.id ? "is-chosen" : ""}`}
            disabled={answered || busy}
            onClick={() => chat.choose(turn.id, option)}
          >
            <span className="ec-option-label">{option.label}</span>
            {option.note && <span className="ec-option-note">{option.note}</span>}
          </button>
        ))}
      </div>
      {!answered && (
        <p className="tiny muted ec-ask-other">
          None of these? Type what you want instead.
        </p>
      )}
    </div>
  );
}

/**
 * A plan, previewed. ⚠ NOTHING HAS HAPPENED YET when this is drawn.
 *
 * ⚠ THE TABLE IS BY FAMILY, NOT BY STEP. Forty rows of "add_transition" is a
 * log, not a summary — what a person checks a plan against is "how much of my
 * film is this going to touch", and that question is answered by four lines.
 * The individual steps are there underneath for anyone who wants them.
 *
 * ⚠ AND A PLAN RESTORED FROM STORAGE IS NOT APPLIABLE. `stale` is set on
 * anything that came back from localStorage: the timeline has been through a
 * refresh since, so its shot numbers may mean something else now. Saying so is
 * better than an Apply button that lands a dissolve on the wrong cut.
 */
function Plan({ turn, chat, readCtx, busy }) {
  const steps = turn.plan?.steps || [];

  // ⚠ DESCRIBED AGAINST THE LIVE FILM, not against the film when the plan
  // arrived. `describeStep` prints "Dissolve after shot 3 — Night market", and
  // that label has to be the one on the timeline the user is looking at now.
  const lines = useMemo(() => {
    if (!steps.length) return [];
    let ctx = {};
    try {
      ctx = { ...readCtx(), caps: capabilities() };
    } catch {
      // The editor is between renders or the panel outlived it. Fall back to
      // the verb's own label rather than losing the preview.
      ctx = {};
    }
    return steps.map((step) => {
      let text = step.verb;
      try {
        text = describeStep(step, ctx) || step.verb;
      } catch {
        text = step.verb;
      }
      return { id: step.id, verb: step.verb, family: FAMILY[step.verb] || "Other", text };
    });
  }, [steps, readCtx]);

  const families = useMemo(() => {
    const counts = new Map();
    for (const line of lines) counts.set(line.family, (counts.get(line.family) || 0) + 1);
    return [...counts.entries()];
  }, [lines]);

  const [showAll, setShowAll] = useState(false);
  const edits = lines.filter((l) => l.verb !== "note").length;

  // ⚠ SOUND IS COUNTED INTO THE BUTTON, because it is an edit the user is about
  // to approve. A button that says "Apply 2 edits" over a plan that also drops a
  // music bed onto the film is a button that under-promises, and the first time
  // anybody notices is when they are looking for where the music came from.
  const cues = turn.sound?.sfx || [];
  const bed = turn.sound?.music || null;
  const soundCount = cues.length + (bed ? 1 : 0);
  const total = edits + soundCount;

  if (turn.applied) {
    const report = turn.soundReport;
    return (
      <div className="ec-plan is-applied">
        <p className="ec-plan-done">
          ✓ Applied — {turn.steps} edit{turn.steps === 1 ? "" : "s"} on the timeline
          {report?.added?.length ? `, plus ${report.added.join(" and ")}` : ""}.
        </p>
        {/* ⚠ WHAT THE LIBRARY COULD NOT FIND IS ON SCREEN, NOT IN A CONSOLE. A
            sound that was promised in the preview and never arrived is the one
            thing about this pass a person cannot see by looking at the timeline —
            an absent whoosh looks exactly like a whoosh nobody asked for. */}
        {report?.missed?.length > 0 && (
          <ul className="ec-drops-list">
            {report.missed.map((why, i) => (
              <li key={i}>{why}</li>
            ))}
          </ul>
        )}
        {chat.scoring && (
          <p className="tiny muted">
            <span className="spinner-inline" /> {chat.scoring}
          </p>
        )}
        {chat.revertable === turn.id && !chat.scoring && (
          <button type="button" className="btn ghost small" onClick={chat.revert}>
            Undo this edit
          </button>
        )}
      </div>
    );
  }

  if (turn.reverted) {
    return <p className="ec-plan-done muted">↩ Put back — the film is as it was.</p>;
  }

  if (turn.stale) {
    return (
      <p className="tiny muted ec-plan-stale">
        This plan was from before the page reloaded, so it can't be applied now —
        the shot numbers may mean something else. Ask again and I'll rewrite it.
      </p>
    );
  }

  // ⚠ A SOUND-ONLY APPLY LOGS NOTHING, because it has no steps to log. Keyed
  // off the run itself as well, or the Apply button would sit there enabled
  // while the library was being searched.
  const runningThis =
    !turn.applied && chat.running && ((turn.log || []).length > 0 || !lines.length);

  return (
    <div className="ec-plan">
      <div className="ec-plan-head">
        <strong>{total}</strong> edit{total === 1 ? "" : "s"}
        {families.map(([name, n]) => (
          <span key={name} className="ec-plan-chip">
            {name} <b>{n}</b>
          </span>
        ))}
        {cues.length > 0 && (
          <span className="ec-plan-chip">
            Sound <b>{cues.length}</b>
          </span>
        )}
        {bed && <span className="ec-plan-chip">Music</span>}
      </div>

      <ul className="ec-plan-steps">
        {(showAll ? lines : lines.slice(0, 5)).map((line) => {
          const done = (turn.log || []).find((l) => l.id === line.id);
          return (
            <li key={line.id} className={done ? `is-${done.state}` : ""}>
              {done?.state === "failed" ? "✕" : done ? "✓" : "·"} {line.text}
            </li>
          );
        })}
      </ul>
      {lines.length > 5 && (
        <button
          type="button"
          className="btn ghost small ec-plan-more"
          onClick={() => setShowAll((v) => !v)}
        >
          {showAll ? "Show less" : `Show all ${lines.length}`}
        </button>
      )}

      {/* ⚠ THE SEARCH TERMS ARE SHOWN, NOT HIDDEN BEHIND "adds sound". What the
          library is asked for is the whole of what decides what arrives, and it
          is the one part of this the user can usefully correct in the next
          message — "no, temple bell, not church bell". */}
      {soundCount > 0 && (
        <ul className="ec-plan-steps ec-plan-sound">
          {cues.map((cue) => (
            <li key={`sfx-${cue.shot}`}>♪ Shot {cue.shot} — “{cue.query}”</li>
          ))}
          {bed && (
            <li key="bed">
              ♫ Music under the whole film — “{bed.query}”
              {bed.mood ? ` (${bed.mood})` : ""}
            </li>
          )}
        </ul>
      )}

      {!runningThis && (
        <div className="ec-plan-actions">
          <button
            type="button"
            className="btn primary small"
            onClick={() => chat.apply(turn.id)}
            disabled={busy || !total}
          >
            Apply {total} edit{total === 1 ? "" : "s"}
          </button>
          <span className="tiny muted">Nothing has changed yet</span>
        </div>
      )}
      {runningThis && (
        <p className="tiny muted">
          <span className="spinner-inline" /> Making the edit…
        </p>
      )}
    </div>
  );
}

/**
 * What could not be used. ⚠ ON SCREEN, NOT IN A CONSOLE.
 *
 * The rule `validatePlan` is built on: what survived is exactly what will
 * happen, and what did not is reported next to it. A quietly shorter plan is how
 * a user comes to believe the assistant did something it never did.
 */
function Drops({ drops }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="ec-drops">
      <button type="button" className="ec-drops-toggle" onClick={() => setOpen((v) => !v)}>
        {drops.length} thing{drops.length === 1 ? "" : "s"} I couldn't use{" "}
        <span aria-hidden="true">{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <ul className="ec-drops-list">
          {drops.map((d, i) => (
            <li key={i}>{d.why}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
