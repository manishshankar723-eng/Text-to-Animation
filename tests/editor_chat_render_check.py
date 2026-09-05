"""THE ✨ AI EDITOR PANEL ACTUALLY RENDERS — the markup, not the file as text.

    python tests/editor_chat_render_check.py

⚠ **THIS EXISTS BECAUSE `npm run build` IS NOT EVIDENCE THAT A SCREEN RENDERS**
(RULEBOOK G7). esbuild reads the module and never evaluates it, so a build stays
green over a component that throws the moment React calls it — a `.map` on
something that is not an array, a destructure of a prop nobody passes, a hook
called under a condition. Every one of those is a white panel and a green build.

⚠ **AND THE PANEL IS THE ONE PART A BROWSER TEST DOES NOT REACH.** `EditorChat`
returns `null` while it is shut, and it is shut in every existing suite —
`editor_director_check.py` mounts the editor with the chat hook in it (which is
worth having, and is how we know the hook does not throw on mount) but never
opens the panel, so the whole of its markup is code no test has run.

So this bundles the REAL component with esbuild and renders it with
`react-dom/server`, against a stub `chat` shaped exactly like `useEditorChat`'s
return. It is not a substitute for opening it in a browser — there is no layout
here, no CSS and no click — but it proves the render path executes and that the
three reply kinds each produce the markup they are supposed to.

⚠ **THE ASSERTIONS ARE ABOUT WHAT THE USER SEES, NOT ABOUT CLASS NAMES**, as far
as that is possible: the question's words, the option's note, the "none of these"
line that is the whole point of the feature, and the fact that a plan's button
says how many edits it would make.

Needs node and `client/node_modules`. Touches no backend and no dollar.
"""

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "client"

failures: list[str] = []


def check(label, good, detail=""):
    print(f"  {'ok  ' if good else 'FAIL'} {label}" + ("" if good else f"  {detail}"))
    if not good:
        failures.append(label)


# ⚠ THE STUB IS THE HOOK'S RETURN, FIELD FOR FIELD. If `useEditorChat` grows a
# field the panel reads, this stub stops having it and the render throws here
# rather than in somebody's editor — which is the main thing a harness like this
# buys over reading the file.
ENTRY = """
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import EditorChat from %(panel)r;

const readCtx = () => ({
  frames: [
    { id: "f1", label: "Street", duration_ms: 3000 },
    { id: "f2", label: "Door", duration_ms: 2000 },
    { id: "f3", label: "Room", duration_ms: 4000 },
  ],
  starts: [0, 3000, 5000],
  texts: [], shapes: [], overlays: [], transitions: [], audioTracks: [],
  totalMs: 9000,
  readTransitions: () => [],
});

const base = {
  turns: [],
  sending: false,
  running: false,
  error: "",
  blocked: "",
  revertable: "",
  config: { dock: "right", transcript_keep: 20 },
  quota: { used: 3, limit: 500 },
  scoring: "",
  send() {}, choose() {}, apply() {}, revert() {}, clear() {}, setError() {},
};

// ⚠ THE SECOND STUB IS `useChatSessions`'s RETURN, FIELD FOR FIELD, for the
// same reason the first one is the agent hook's: a field the panel reads that
// the hook stops handing back throws HERE rather than in somebody's editor.
const noChats = {
  turns: [], sessions: [], active: null, activeId: "", limit: 40,
  listing: false, opening: false, error: "", saves: true, full: false,
  setTurns() {}, newChat() {}, open() {}, rename() {}, remove() {},
  clearActive() {}, refresh() {},
};

const oneChat = {
  ...noChats,
  activeId: "s1",
  active: { session_id: "s1", title: "Sound + transitions", turn_count: 3,
            created_at: "", updated_at: "2026-09-05T11:00:00Z" },
  sessions: [
    { session_id: "s1", title: "Sound + transitions", turn_count: 3,
      created_at: "", updated_at: "2026-09-05T11:00:00Z" },
    { session_id: "s2", title: "", turn_count: 1,
      created_at: "", updated_at: "2026-09-03T09:00:00Z" },
  ],
};

const draw = (chat, extra = {}) =>
  renderToStaticMarkup(
    React.createElement(EditorChat, {
      open: true, onClose() {}, chat, store: oneChat, readCtx,
      dock: "right", greeting: "", ...extra,
    })
  );

const out = {};

// -------------------------------------------------------------- shut and open
out.shut = renderToStaticMarkup(
  React.createElement(EditorChat, {
    open: false, onClose() {}, chat: base, store: oneChat, readCtx,
  })
);
out.empty = draw(base);

// ------------------------------------------------------------------ an answer
out.answer = draw({
  ...base,
  turns: [
    { id: "u1", role: "user", kind: "text", text: "how long is this?" },
    { id: "a1", role: "agent", kind: "answer", text: "Three shots, 9.0s." },
  ],
});

// --------------------------------------------------------- ⭐ an ask
out.ask = draw({
  ...base,
  turns: [
    { id: "u1", role: "user", kind: "text", text: "add music" },
    { id: "a1", role: "agent", kind: "ask", text: "",
      ask: {
        question: "What kind of music?",
        reason: "target",
        allow_other: true,
        options: [
          { id: "o1", label: "Soft piano", note: "for the quiet scenes" },
          { id: "o2", label: "Dhol", note: "celebration mood" },
        ],
      } },
  ],
});
// The same one, already answered — the chips must stop being live.
out.asked = draw({
  ...base,
  turns: [
    { id: "a1", role: "agent", kind: "ask", text: "", chosen: "o2",
      ask: { question: "What kind of music?", allow_other: true,
             options: [{ id: "o1", label: "Soft piano" }, { id: "o2", label: "Dhol" }] } },
  ],
});

// -------------------------------------------------------------------- a plan
const planTurn = {
  id: "a1", role: "agent", kind: "plan", text: "Softening the cuts.",
  steps: 2,
  plan: { version: 1, summary: "s", steps: [
    { id: "p1", verb: "add_transition", args: { after_frame_id: "f1", kind: "dissolve", duration_ms: 500 } },
    { id: "p2", verb: "add_transition", args: { after_frame_id: "f2", kind: "dissolve", duration_ms: 500 } },
  ] },
  drops: [{ what: "step", why: "there is no \\u201cteleport\\u201d verb" }],
};
out.plan = draw({ ...base, turns: [planTurn] });
out.applied = draw({ ...base, revertable: "a1",
  turns: [{ ...planTurn, applied: true }] });
out.stale = draw({ ...base,
  turns: [{ id: "a1", role: "agent", kind: "plan", text: "old", stale: true }] });

// -------------------------------------------------------------- the sound
// ⚠ SOUND IS COUNTED INTO THE BUTTON. A plan that also drops a music bed onto
// the film and says "Apply 1 edit" under-promises, and the first time anybody
// notices is when they go looking for where the music came from.
const soundTurn = {
  id: "a1", role: "agent", kind: "plan", text: "A bell, and something under it.",
  plan: { version: 1, summary: "s", steps: [
    { id: "p1", verb: "add_transition", args: { after_frame_id: "f1", kind: "dissolve", duration_ms: 500 } },
  ] },
  sound: {
    sfx: [{ shot: 2, query: "temple bell" }],
    music: { query: "soft sitar", mood: "warm" },
  },
};
out.sound = draw({ ...base, turns: [soundTurn] });
// A sound-only plan: no steps at all, and Apply must still be live.
out.soundOnly = draw({ ...base, turns: [{ ...soundTurn, plan: { version: 1, summary: "", steps: [] } }] });
out.scored = draw({ ...base, revertable: "a1", turns: [{
  ...soundTurn, applied: true, steps: 1,
  soundReport: { added: ["1 sound effect", "a music bed"], missed: ["church bell — no usable sound was found for it"] },
}] });
out.scoring = draw({ ...base, scoring: "Finding 2 sounds…", running: true, turns: [{
  ...soundTurn, applied: true, steps: 1,
}] });

// ------------------------------------------- one project, many conversations
out.chatNamed = draw(base);
// A panel that has never been spoken to: no chat exists yet, so there is
// nothing to rename and the footer cannot promise it is saved.
out.chatFresh = draw(base, { store: noChats });
// The project itself does not exist yet either — the first message creates it.
out.chatUnsaved = draw(base, { store: { ...noChats, saves: false } });
// ⚠ A PROJECT WITH NO ROOM LEFT. The operator's ceiling is 1 and the one chat
// there has been typed in, so the server would refuse another — `isFull` in
// `chat_sessions.js` is what works that out, and the ＋ must ask BEFORE it opens
// a blank panel.
out.chatFull = draw(base, {
  store: {
    ...oneChat,
    limit: 1,
    sessions: [oneChat.sessions[0]],
    full: true,
  },
});
// The same project, but the panel is sitting on an UNSAVED chat — what happens
// when the ceiling is lowered in the admin panel while somebody is part-way
// through. Nothing here can be saved, and the footer has to say so.
out.chatFullUnsaved = draw(base, {
  store: {
    ...oneChat,
    limit: 1,
    sessions: [oneChat.sessions[0]],
    activeId: "",
    active: null,
    full: true,
  },
});
// A chat being fetched. ⚠ THE GREETING MUST NOT SHOW HERE — an empty log under
// the welcome text is what a BRAND NEW chat looks like, and showing it while an
// old conversation is on its way says the chat came back empty.
out.chatOpening = draw(base, { store: { ...oneChat, opening: true } });
// The store is unreachable. The conversation still works; the footer says so.
out.chatOffline = draw(base, {
  store: { ...oneChat, error: "This chat is not being saved right now." },
});

// ----------------------------------------------------------- the three docks
out.dockRight = draw(base, { dock: "right" });
out.dockSide = draw(base, { dock: "sidebar" });
out.dockFloat = draw(base, { dock: "float" });
out.dockUser = draw(base, { dock: "user" });

// ------------------------------------------- how see-through the operator set
out.solid = draw(base, { dock: "right", opacity: 100 });
out.seeThrough = draw(base, { dock: "right", opacity: 60 });
// ⚠ ZERO IS A REAL SETTING, NOT AN ABSENT ONE. `opacity: 0` went through an
// `or 100` on the server once and came back as its opposite; the client has the
// same trap in `?? 100` / `|| 100`.
out.invisible = draw(base, { dock: "right", opacity: 0 });
// ⚠ THE BLUR IS A SECOND SETTING AND IT IS GATED ON BOTH. `backdrop-filter` at
// any value promotes the panel to its own compositing layer over a playing
// timeline, so it is only worth paying for when there is something showing
// through AND the operator asked for it.
out.blurred = draw(base, { dock: "right", opacity: 60, blur: 16 });
out.blurOnSolid = draw(base, { dock: "right", opacity: 100, blur: 16 });
out.seeThroughNoBlur = draw(base, { dock: "right", opacity: 60, blur: 0 });

// ------------------------------------------------- blocked, and unlimited
out.blocked = draw({ ...base, blocked: "You've used all 500 AI Editor messages this month." });
out.unlimited = draw({ ...base, quota: { used: 12, limit: null } });

process.stdout.write(JSON.stringify(out));
"""


def render() -> dict | None:
    if not shutil.which("node"):
        print("  node is not on PATH — nothing checked.")
        return None
    if not (CLIENT / "node_modules" / "react-dom").exists():
        print("  client/node_modules is missing — run `cd client && npm install` first.")
        return None

    work = tempfile.mkdtemp(prefix="ec_render_")
    try:
        # ⚠ THE ENTRY LIVES INSIDE `client/`, NOT IN THE TEMP DIR. esbuild resolves
        # `react` and `react-dom/server` from the entry's own folder upwards, and an
        # entry in %TEMP% would resolve neither — bundling would fail with a
        # "could not resolve" that reads like the component is broken.
        entry = CLIENT / "__ec_render_entry.jsx"
        bundle = os.path.join(work, "bundle.cjs")
        panel = (CLIENT / "src/components/EditorChat.jsx").as_posix()
        entry.write_text(ENTRY % {"panel": "./" + os.path.relpath(panel, CLIENT).replace("\\", "/")},
                         encoding="utf-8")
        try:
            build = subprocess.run(
                [str(CLIENT / "node_modules/.bin/esbuild.cmd" if os.name == "nt"
                     else CLIENT / "node_modules/.bin/esbuild"),
                 str(entry), "--bundle", "--platform=node", "--format=cjs",
                 "--loader:.js=jsx", "--jsx=automatic", f"--outfile={bundle}",
                 "--log-level=error"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(CLIENT),
            )
            if build.returncode != 0:
                print("    esbuild said:", (build.stderr or "").strip()[:1500])
                return None
            proc = subprocess.run(
                ["node", bundle],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(CLIENT),
            )
            if proc.returncode != 0:
                # ⚠ THIS IS THE FAILURE THIS FILE EXISTS FOR: the component threw
                # while React was calling it, which a green build cannot see.
                print("    node said:", (proc.stderr or "").strip()[:2000])
                return None
            return json.loads(proc.stdout)
        finally:
            entry.unlink(missing_ok=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def text_of(markup: str) -> str:
    """The markup as a person would READ it.

    ⚠ JSX ESCAPES EVERY APOSTROPHE TO `&#x27;`, so `"couldn't use" in markup` is
    an assertion that can never pass however right the component is. Three checks
    here failed that way on the first run — a harness that reports the component
    broken when the harness is wrong is worse than no harness, so the unescape
    happens once, here, rather than being remembered at each call.
    """
    return html.unescape(markup)


def main() -> int:
    out = render()
    if out is None:
        return 2
    out = {k: text_of(v) for k, v in out.items()}

    print("\n1 · It renders at all, and it renders NOTHING when it is shut\n")
    check("shut, it draws nothing", out["shut"] == "", out["shut"][:80])
    check("open, it draws a panel", "ec-panel" in out["empty"])
    check("…with the title on it", "AI Editor" in out["empty"])
    check("…and a way out", "modal-close" in out["empty"])
    # ⚠ THE EMPTY STATE SAYS WHAT TO TYPE, NOT WHAT THE FEATURE IS.
    check("…and three real example sentences, not a greeting",
          out["empty"].count("ec-example") >= 3)
    check("…one of which is a vague ask, so the asking is discoverable",
          "urgent" in out["empty"])

    print("\n2 · The three reply kinds each draw what they should\n")
    check("a person's line is drawn as theirs", "is-user" in out["answer"])
    check("…and the answer beside it", "Three shots, 9.0s." in out["answer"])
    check("an answer draws no Apply button",
          "ec-plan-actions" not in out["answer"] and "Apply " not in out["answer"],
          out["answer"][-200:])

    print("\n3 · ⭐ The question with options\n")
    check("the question is on screen", "What kind of music?" in out["ask"])
    check("both options are drawn", "Soft piano" in out["ask"] and "Dhol" in out["ask"])
    check("…each with the half-line that makes it a decision",
          "celebration mood" in out["ask"])
    # ⚠ THE LINE THE WHOLE FEATURE TURNS ON.
    check("⭐ 'none of these' is offered", "None of these" in out["ask"])
    check("…and the chips are live", "disabled" not in out["ask"].split("ec-ask-options")[1][:400])
    check("an answered question is marked", "is-answered" in out["asked"])
    check("…the chosen one is shown as chosen", "is-chosen" in out["asked"])
    check("…the chips are dead", "disabled" in out["asked"].split("ec-ask-options")[1][:600])
    check("…and 'none of these' is gone once answered", "None of these" not in out["asked"])

    print("\n4 · A plan is a proposal, never a report\n")
    check("the steps are described in the film's own words",
          "Street" in out["plan"] or "Dissolve" in out["plan"] or "dissolve" in out["plan"],
          out["plan"][-400:])
    check("the button says how many edits", "Apply 2 edits" in out["plan"])
    # ⚠ THE SENTENCE THAT STOPS SOMEBODY THINKING IT ALREADY HAPPENED.
    check("…and it says nothing has changed yet",
          "Nothing has changed yet" in out["plan"])
    check("what could not be used is ON SCREEN",
          "couldn't use" in out["plan"])
    check("an applied plan reports the count", "Applied" in out["applied"])
    check("…and offers Undo", "Undo this edit" in out["applied"])
    check("an unapplied plan offers no Undo", "Undo this edit" not in out["plan"])
    # ⚠ A PLAN FROM BEFORE A REFRESH IS A TRAP, NOT A SAVING.
    check("a stale plan cannot be applied",
          "ec-plan-actions" not in out["stale"] and "Apply " not in out["stale"])
    check("…and says why", "can't be applied" in out["stale"])

    print("\n5 · Sound is part of the same approval, and the same undo\n")
    # ⚠ 1 step + 1 cue + 1 bed = 3. A button that says "1 edit" here is a button
    # that under-promises, which is how a music bed becomes a mystery.
    check("sound is counted into the Apply button", "Apply 3 edits" in out["sound"],
          out["sound"][-300:])
    check("the cued sound is named, not hidden behind 'adds sound'",
          "temple bell" in out["sound"] and "Shot 2" in out["sound"])
    check("…and so is the bed, with its mood",
          "soft sitar" in out["sound"] and "warm" in out["sound"])
    check("the chips say how much of each", "Sound" in out["sound"] and "Music" in out["sound"])
    # ⭐ SOUND WITH NO STEPS IS STILL AN APPLY.
    check("⭐ a sound-only plan still offers Apply", "Apply 2 edits" in out["soundOnly"],
          out["soundOnly"][-300:])
    check("what was added is reported afterwards",
          "1 sound effect" in out["scored"] and "a music bed" in out["scored"])
    check("…and what the library could not find is ON SCREEN",
          "no usable sound was found" in out["scored"])
    check("while it searches, it says so", "Finding 2 sounds" in out["scoring"])
    check("…and Undo is withheld until it has finished",
          "Undo this edit" not in out["scoring"] and "Undo this edit" in out["scored"])

    print("\n6 · All three docks are real, and the switcher only appears when it should\n")
    check("the right-hand dock", "ec-dock-right" in out["dockRight"])
    check("the sidebar dock", "ec-dock-sidebar" in out["dockSide"])
    check("the floating window", "ec-dock-float" in out["dockFloat"])
    check("a locked deployment shows no dock switcher",
          "ec-dock-pick" not in out["dockRight"] and "ec-dock-pick" not in out["dockSide"])
    check("'let each person choose' shows it", "ec-dock-pick" in out["dockUser"])
    check("…and it offers the floating window too", "Floating window" in out["dockUser"])

    print("\n6a · It can be moved and it can be resized — and those are different\n")
    # ⚠ THE FLOATING WINDOW IS PLACED BY JAVASCRIPT, NOT BY THE STYLESHEET, so
    # the geometry has to be in the markup or the panel opens full-height in the
    # corner. `left:` is the one that proves the inline style went on at all.
    check("the floating window carries its own position",
          "left:" in out["dockFloat"] and "top:" in out["dockFloat"],
          out["dockFloat"][:160])
    # ⚠ ONLY THE FLOATING ONE MOVES. A `left:` on the right-hand dock would mean
    # the panel had come off the edge it is supposed to be pinned to.
    check("…and the pinned docks do not",
          "left:" not in out["dockRight"].split(">")[0])
    check("the floating window has a corner to resize by",
          "ec-grip" in out["dockFloat"])
    # ⚠ ALL FOUR SIDES AND ALL FOUR CORNERS — asked for outright: *"abhi ek taraf
    # se hi chota bara karta hun, mai chahta hun charo taraf se"*. A handle that
    # silently stops rendering is invisible until somebody tries to drag that
    # edge, so every compass point is named here rather than counted.
    for _edge in ("n", "s", "e", "w", "ne", "nw", "se", "sw"):
        check(f"…and a handle on its {_edge} side",
              f"ec-resize-{_edge}" in out["dockFloat"])
    # ⚠ ONE TAB STOP, NOT EIGHT. The corner is the focusable one and the arrow
    # keys on it already resize both axes; seven more stops between the composer
    # and the page would be the cost of nothing.
    check("…and only one of the eight is a tab stop",
          out["dockFloat"].count("ec-resize ec-resize-") == 8
          and out["dockFloat"].count("tabindex") == 1,
          out["dockFloat"].count("tabindex"))
    check("…and it says so on hover, not in the panel",
          "Drag to resize" in out["dockFloat"])
    check("the title bar says it can be dragged",
          "Drag to move the panel" in out["dockFloat"])
    # ⚠ THE PINNED DOCKS RESIZE TOO — by the workspace's own seam, not by a
    # second handle written for this panel. `an-split` is `PaneSplitter`'s class.
    # ⚠ AND IT IS ABSENT ON THE FIRST PAINT ON PURPOSE: the width is measured off
    # the real element in an effect, and `react-dom/server` runs no effects. What
    # this pins is that the seam is NEVER on the floating window, which has a
    # corner instead — two resize affordances on one panel is the bug.
    check("the floating window has no edge seam", "an-split" not in out["dockFloat"])

    print("\n6c · One project, many chats — and the bar that moves between them\n")
    # ⚠ THE WHOLE POINT OF THE FEATURE, AND THE PART A BROWSER TEST CANNOT SEE
    # WITHOUT A LOGIN. Asked for outright: *"user new chat bana kar alag alag baat
    # kar sake … aur sab chat save hona chahiye, us project mai dekh sake fir
    # baad mai"*.
    check("the chat bar is drawn", "ec-sess" in out["chatNamed"])
    check("…carrying this chat's name", "Sound + transitions" in out["chatNamed"])
    check("…a way to see the others", "Chats in this project" in out["chatNamed"])
    check("…and a way to start a new one", "New chat" in out["chatNamed"])
    # ⚠ THREE CONTROLS, NOT A TAB PER CHAT. A tab strip is the obvious reading of
    # "tab ka function" and it is wrong for a 300–760px panel: two tabs fill it and
    # the NAMES — the only thing telling two conversations apart — are the first
    # thing it truncates. The list holds them at full length instead, so a SECOND
    # chat in the store must add nothing at all to this row.
    check("…and the bar does not grow a row per chat",
          out["chatNamed"].count("ec-sess-btn") == 2,
          out["chatNamed"].count("ec-sess-btn"))
    # The list itself is a popover and opens on a click, which `renderToStaticMarkup`
    # cannot do — its rows are covered by `tests/chat_store_check.mjs` instead.
    check("…the list is shut until it is asked for", "ec-hist" not in out["chatNamed"])

    # ⚠ A CHAT IS NOT CREATED UNTIL SOMEBODY SPEAKS IN IT, so a fresh panel has
    # nothing to name and says so rather than offering a rename that goes nowhere.
    check("a panel with no chat yet still draws the bar", "ec-sess" in out["chatFresh"])
    check("…with the placeholder name", "New chat" in out["chatFresh"])
    check("…and the rename is disabled, not hidden",
          "ec-sess-name" in out["chatFresh"] and "disabled" in out["chatFresh"])

    # ⚠ THE FOOTER USED TO SAY "saved in this browser only" AND THAT WAS TRUE.
    # It is not any more — the conversation goes to the server with the project.
    # A promise about somebody's work is the worst place to leave a stale string.
    check("⚠ the footer no longer claims browser-only storage",
          "saved in this browser only" not in out["chatNamed"])
    check("…it says the chat is kept with the project",
          "saved with this project" in out["chatNamed"])
    check("…and on a project that does not exist yet, it says when",
          "saved once you send the first message" in out["chatUnsaved"])
    check("a store that is down says so in the footer, not in a modal",
          "not being saved right now" in out["chatOffline"]
          and "ec-foot-warn" in out["chatOffline"])

    # ⚠ "OPENING…" RATHER THAN THE GREETING. An empty log under the welcome text
    # is what a BRAND NEW chat looks like; showing it while an old conversation is
    # still on its way says the chat came back empty.
    check("a chat being fetched says it is opening", "Opening" in out["chatOpening"])
    check("…and does NOT draw the new-chat greeting over it",
          "ec-empty" not in out["chatOpening"])
    check("…while a genuinely new chat does", "ec-empty" in out["chatNamed"])

    print("\n6d · ⚠ THE RENAME BOX KEEPS WHAT IS TYPED INTO IT\n")
    # ⚠ THIS SHIPPED BROKEN AND THE SYMPTOM WAS ABSURD: the box could never hold
    # more than ONE character. Type "c", type "a", and it says "a". Reported
    # exactly that way — *"ek letter likh raha hun, dusra letter likhta hun to
    # pahla ko hata de raha hai"*.
    #
    # THE CAUSE: opening the box also SELECTS the old name — correct, because it
    # opens over a name that is being replaced — and the select was re-running on
    # every keystroke, so each new letter landed on a fully highlighted field and
    # replaced what was already there.
    #
    # ⚠ AND THE APP ALREADY KNEW. `MediaBin.jsx`'s rename box carries this exact
    # fix, with a comment naming this exact trap; this panel was written without
    # reading it. It now uses the identical idiom — one way to open a rename box,
    # not two.
    #
    # ⚠ IT CANNOT BE CAUGHT BY RENDERING. `renderToStaticMarkup` runs no effects
    # and there is no DOM here — and jsdom is a dependency this project does not
    # have and does not want (see the frontend section of AGENTS.md). So this
    # reads the mechanism out of the source. Proved to FAIL with the guard
    # removed.
    src_text = (CLIENT / "src/components/EditorChat.jsx").read_text(encoding="utf-8")
    bin_text = (CLIENT / "src/components/MediaBin.jsx").read_text(encoding="utf-8")

    check(
        "the rename box is a controlled input",
        bool(re.search(r'className="ec-sess-input".*?value=\{(\w+)\}', src_text, re.S)),
    )

    # The ref callback that opens THAT box — read as the text between `ref={(el)`
    # and the `ec-sess-input` class, so this cannot accidentally test some other
    # field's focus code.
    m_ref = re.search(
        r"ref=\{\(el\) => \{(.*?)\}\}.*?className=\"ec-sess-input\"", src_text, re.S
    )
    check("it focuses itself when it opens", bool(m_ref))
    body = m_ref.group(1) if m_ref else ""
    check("…and selects the old name, so it need not be cleared first",
          ".select()" in body)
    # ⚠ THE ONE THAT ACTUALLY REGRESSED. A ref callback runs on EVERY render, so
    # an unguarded select() re-highlights the field after each letter.
    check("⚠ …GUARDED, so it does not re-select on every keystroke",
          "document.activeElement !== el" in body,
          " ".join(body.split())[:120])
    # Plain autofocus lets the browser scroll the field into view — MediaBin's
    # other reason for using a callback, and it applies here too.
    check("…and it does not scroll the panel to do it", "preventScroll" in body)
    # ⚠ ONE IDIOM, IN BOTH PLACES. Two ways to open a rename box in one app is two
    # places to get this identical bug.
    check("…the same idiom MediaBin's rename box already used",
          "document.activeElement !== el" in bin_text)

    print("\n6e · ⚠ A FULL PROJECT REFUSES ＋ BEFORE IT OPENS A BLANK PANEL\n")
    # ⚠ THIS SHIPPED MISSING AND WAS REPORTED FROM A LIVE DEPLOYMENT. With the
    # operator's ceiling set to 1 and one real chat already saved, ＋ answered
    # with a cheerful empty "New chat" — and the refusal only arrived once a
    # whole message had been typed and the autosave came back 409:
    # *"maine admin panel mai ek likha, to yaha pe new chat open hua — kya ye
    # sahi hai?"*. It was not: the panel promised a chat it could not save.
    # ⚠ THE ＋ BUTTON'S OWN TAG, NOT THE WHOLE PANEL. The composer's Send is
    # disabled whenever the box is empty, so "is there a `disabled` anywhere"
    # answers yes on every render and proves nothing.
    def plus_tag(markup):
        m = re.search(r'<button[^>]*aria-label="New chat"[^>]*>', markup)
        return m.group(0) if m else ""

    check("the ＋ is live when there is room", bool(plus_tag(out["chatNamed"])))
    check("…and not disabled", "disabled" not in plus_tag(out["chatNamed"]),
          plus_tag(out["chatNamed"]))

    # ⚠ DISABLED, NOT HIDDEN. A control that vanishes at a limit reads as a
    # feature that broke; one that is visibly unavailable, with the reason on
    # hover, is a rule somebody can act on.
    check("⚠ a full project greys the ＋ out", "disabled" in plus_tag(out["chatFull"]),
          plus_tag(out["chatFull"]))
    check("…and it is still on screen", bool(plus_tag(out["chatFull"])))
    # The way out is in the sentence — this is the only place it is said, and it
    # is said on hover rather than as another line of chrome in a narrow panel.
    check("…saying why, and how to get out of it",
          "delete one to start another" in out["chatFull"])

    # ⚠ AND THE FOOTER SAYS IT BEFORE A WORD IS TYPED when the panel is sitting on
    # an unsaved chat in a full project — the ceiling can be lowered in the admin
    # panel while somebody is part-way through, and "saved with this project"
    # would then be a promise this panel cannot keep.
    check("⚠ an unsaved chat in a full project does not promise it is saved",
          "saved with this project" not in out["chatFullUnsaved"])
    check("…it says the project is full instead",
          "delete one to save this" in out["chatFullUnsaved"])
    check("…in the warning colour, because it is a promise it cannot keep",
          "ec-foot-warn" in out["chatFullUnsaved"])

    print("\n6b · How see-through it is, and whose decision that is\n")
    check("the operator's number reaches the panel", "--ec-opacity:60" in
          out["seeThrough"].replace("--ec-opacity: 60", "--ec-opacity:60"),
          out["seeThrough"][:200])
    check("…and see-through is a class, so a solid panel pays for nothing",
          "is-see-through" in out["seeThrough"])
    check("a solid panel is not made see-through", "is-see-through" not in out["solid"])
    check("⚠ zero survives as zero, and is not read as 'no setting'",
          "--ec-opacity:0" in out["invisible"].replace("--ec-opacity: 0", "--ec-opacity:0")
          and "is-see-through" in out["invisible"],
          out["invisible"][:200])
    # ⚠ THE CUSTOMER HAS NO SLIDER FOR THIS, AND MUST NOT GROW ONE. A panel a
    # customer can fade until they cannot read it has no way back — see the note
    # at the top of `EditorChat.jsx`.
    check("the operator's blur reaches the panel", "--ec-blur:16" in
          out["blurred"].replace("--ec-blur: 16", "--ec-blur:16")
          and "is-blurred" in out["blurred"],
          out["blurred"][:220])
    # ⚠ BOTH GATES, AND EACH ONE ON ITS OWN. A blur on a solid panel is a
    # per-frame GPU cost over a playing timeline for an effect nobody can see,
    # and a `blur(0px)` costs the same layer as a `blur(16px)`.
    check("…and a blur on a SOLID panel is not paid for",
          "is-blurred" not in out["blurOnSolid"])
    check("…nor is a blur of zero on a see-through one",
          "is-blurred" not in out["seeThroughNoBlur"]
          and "is-see-through" in out["seeThroughNoBlur"])
    check("the customer is offered no transparency control",
          "opacity" not in out["dockUser"].lower().replace("--ec-opacity", "")
          and "blur" not in out["dockUser"].lower().replace("--ec-blur", ""))

    print("\n7 · The allowance is shown before it runs out, not at the refusal\n")
    check("the count is on screen", "3 of 500 messages" in out["empty"])
    check("…and hidden when it is unlimited", "messages this month" not in out["unlimited"])
    # ⚠ PINNED BECAUSE IT WAS WRONG: the hint used to promise an Apply button on
    # every turn, including an empty chat that has nothing to apply.
    check("the composer's promise is true even with nothing to apply",
          "before it happens" in out["empty"] and "press Apply" not in out["empty"])
    check("a blocked chat says so", "all 500 AI Editor messages" in out["blocked"])
    check("…and the composer is disabled with it",
          out["blocked"].count("disabled") > out["empty"].count("disabled"))

    print()
    if failures:
        print(f"✗ {len(failures)} check(s) failed:")
        for name in failures:
            print(f"    - {name}")
        return 1
    print("✓ the panel renders, and all three reply kinds draw what they should")
    return 0


if __name__ == "__main__":
    sys.exit(main())
