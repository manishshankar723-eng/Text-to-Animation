// useUndoStack.js — Ctrl+Z / Ctrl+Shift+Z for the animatic editor.
//
// History of the WHOLE document, because that is the unit a person means by
// "undo": one stack, not one per layer. Entries hold the actual state arrays
// (not JSON), so restoring is exact and costs nothing to serialise.
//
// The caller owns the document. This hook only watches it — it is handed the
// current `doc` and its `signature`, and hands back `apply(snapshot)` calls when
// the user steps through the stack.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * @param doc        the current document object (identity changes every edit)
 * @param signature  a string that changes only when the CONTENT changes
 * @param loadedRef  a ref that is false until the project has loaded
 * @param apply      writes a snapshot back onto the document's state
 * @param onNotice   the editor's status line
 */
export default function useUndoStack({ doc, signature, loadedRef, apply, onNotice }) {
  const historyRef = useRef({ past: [], future: [], present: null, sig: null, lastPush: 0 });
  // Bumped on every history change purely so the toolbar's enabled/disabled
  // state re-renders — the stack itself lives in the ref.
  const [historyTick, setHistoryTick] = useState(0);

  useEffect(() => {
    const h = historyRef.current;
    // ⚠ Nothing is recorded until the project has LOADED. An editor mounts with
    // empty frames/texts/shapes and fills them from the server a moment later;
    // recording that as an edit made the very first Ctrl+Z restore the empty
    // document and wipe the animatic on screen. The load handler resets this
    // ref (see `reset` below), so the loaded state — not the empty one — is
    // where history begins.
    if (!loadedRef.current) return;
    if (h.present === null || h.restoring) {
      // First render, or the state we just restored — neither is a new edit.
      h.restoring = false;
      h.present = doc;
      h.sig = signature;
      return;
    }
    if (h.sig === signature) return; // identity changed, content didn't
    // Coalesce: a drag fires dozens of changes a second, and undoing one pixel
    // at a time is useless.
    //
    // TWO rules, because the timer alone was not enough. Inside a GESTURE — a
    // pointer is down and being dragged — only the first change is recorded, so
    // the whole drag is one undo no matter how long it lasts. That matters most
    // for the thing this was added for: dragging a keyframe or an opacity
    // slider slowly is easy to do for several seconds, and on the timer alone
    // it left one undo entry per half second, so Ctrl+Z walked the value back
    // in steps instead of putting it where it started.
    //
    // Outside a gesture the old half-second burst rule still applies: it covers
    // held arrow keys and typing, which have no pointer to bracket them.
    const inGesture = h.gesture;
    if (inGesture ? h.gestureFirst : Date.now() - h.lastPush > 500) {
      h.past = [...h.past.slice(-49), h.present];
      h.lastPush = Date.now();
      h.gestureFirst = false;
      setHistoryTick((t) => t + 1);
    }
    h.future = [];
    h.present = doc;
    h.sig = signature;
  }, [signature, doc, loadedRef]);

  /**
   * Throw the stack away and start again from wherever the document is now.
   * Called once the project has loaded: anything recorded before that point
   * describes an editor that hadn't loaded yet.
   */
  const reset = useCallback(() => {
    historyRef.current = { past: [], future: [], present: null, sig: null, lastPush: 0 };
    setHistoryTick((t) => t + 1);
  }, []);

  /**
   * Bracket a drag so the whole thing is ONE undo.
   *
   * Called on pointer down and pointer up by everything that drags a value:
   * timeline clips, frame edges, the shapes on the monitor, keyframe diamonds
   * and the opacity sliders. A gesture that never changes anything records
   * nothing, because the history effect only fires on a real content change.
   */
  const setGesture = useCallback((active) => {
    const h = historyRef.current;
    h.gesture = active;
    if (active) {
      h.gestureFirst = true;
    } else {
      // The next unrelated edit starts a fresh entry rather than being absorbed
      // into the gesture that just ended.
      h.lastPush = 0;
    }
  }, []);

  /**
   * Spread onto anything draggable: `<input type="range" {...gestureProps} />`.
   *
   * ⚠ The END of the gesture is caught on the WINDOW, not on the element. A
   * pointer released outside the control it started on never delivers a
   * pointerup to that control, and a gesture that is never closed swallows
   * every later edit into one undo entry — a far worse bug than the one this
   * exists to fix. The window always sees it.
   */
  const gestureProps = {
    onPointerDown: () => {
      setGesture(true);
      const end = () => {
        setGesture(false);
        window.removeEventListener("pointerup", end);
        window.removeEventListener("pointercancel", end);
      };
      window.addEventListener("pointerup", end);
      window.addEventListener("pointercancel", end);
    },
  };

  const applyDoc = useCallback(
    (snapshot) => {
      historyRef.current.restoring = true;
      apply(snapshot);
    },
    [apply]
  );

  const undo = useCallback(() => {
    const h = historyRef.current;
    if (!h.past.length) return;
    const previous = h.past[h.past.length - 1];
    h.past = h.past.slice(0, -1);
    h.future = [h.present, ...h.future].slice(0, 50);
    h.lastPush = 0; // the next real edit starts a fresh entry
    applyDoc(previous);
    setHistoryTick((t) => t + 1);
    onNotice("Undo");
  }, [applyDoc, onNotice]);

  const redo = useCallback(() => {
    const h = historyRef.current;
    if (!h.future.length) return;
    const next = h.future[0];
    h.future = h.future.slice(1);
    h.past = [...h.past.slice(-49), h.present];
    h.lastPush = 0;
    applyDoc(next);
    setHistoryTick((t) => t + 1);
    onNotice("Redo");
  }, [applyDoc, onNotice]);

  // Read off the ref, but recomputed when the tick says the stack moved — the
  // stack itself must not be state, or every push would re-render the editor.
  const { canUndo, canRedo } = useMemo(
    () => ({
      canUndo: historyRef.current.past.length > 0,
      canRedo: historyRef.current.future.length > 0,
    }),
    [historyTick]
  );

  return { undo, redo, canUndo, canRedo, setGesture, gestureProps, reset };
}
