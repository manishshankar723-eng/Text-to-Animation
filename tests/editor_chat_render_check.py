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

const draw = (chat, extra = {}) =>
  renderToStaticMarkup(
    React.createElement(EditorChat, {
      open: true, onClose() {}, chat, readCtx, dock: "right", greeting: "", ...extra,
    })
  );

const out = {};

// -------------------------------------------------------------- shut and open
out.shut = renderToStaticMarkup(
  React.createElement(EditorChat, { open: false, onClose() {}, chat: base, readCtx })
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
