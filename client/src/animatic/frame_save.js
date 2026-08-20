// frame_save.js — EXACTLY WHAT A PICTURE CLIP LOOKS LIKE WHEN IT IS SAVED.
//
// One function, and it lives in its own module for two reasons.
//
// ⚠ IT IS THE SAME SHAPE THE AUTOSAVE SENDS AND THE DIRTY-CHECK COMPARES. When
// those two were written out separately they drifted, and a field missing from
// the second one is worse than a field missing from the first: the document does
// not look changed, so the autosave never fires and Save believes there is
// nothing to write. The edit is not lost in transit — it is never sent.
//
// ⚠ AND IT IS PURE, so `tests/frame_save_fields_check.py` can call it under node
// and compare what comes out against `AnimaticFrame` in `server/schemas.py`. It
// used to sit inside `useAnimaticProject.js`, which imports React and therefore
// cannot be loaded outside a browser — so the one thing about it worth checking
// automatically could not be. Same rule as `selection.js` and `scene.js`: the
// logic that has a right answer lives where a test can reach it.

/**
 * @param f a picture clip as the editor holds it
 * @returns the same clip as the server's `AnimaticFrame`
 *
 * `url` is deliberately absent: the server fills it on read and ignores it on
 * write, so sending it back would put a stale path in the saved document.
 *
 * ⚠⚠ THIS IS A WHITELIST, AND IT HAS FALLEN BEHIND THE SCHEMA TWICE. A field the
 * schema gains and this function does not mention is computed by the editor,
 * drawn in the monitor, and then thrown away on the way to the server — with no
 * error anywhere, because dropping a key is not a failure. The first time it was
 * `scale`/`x`/`y`/`opacity`/`keyframes`, so Phase 1's motion never survived a
 * reload. The second time it was five fields:
 *
 *   · `track` / `start_ms` — WHICH ROW A CLIP IS ON and WHERE IT SITS, i.e. the
 *     whole multi-track timeline. Every clip came back on track 0 with no start,
 *     so the rows collapsed into one on reload and the clips re-laid themselves
 *     end to end. Reported as "when i go back then i come so i look same
 *     previous arrangement of ecah layer of clip" — there was nothing saved to
 *     come back to, and moving a clip between rows never even marked the
 *     document dirty.
 *   · `effects` / `mask` / `blend` — the clip's whole LOOK. Every colour grade,
 *     every mask, every blend mode, gone on reload.
 *
 * `tests/frame_save_fields_check.py` compares this against the schema and fails
 * on the next field that goes missing. Add to BOTH, or add the field to
 * `SERVER_OWNED` in that test and say there why it must not be sent.
 */
export function frameForSave(f) {
  return {
    id: f.id,
    src: f.src,
    duration_ms: f.duration_ms,
    label: f.label || "",
    kind: f.kind || "image",
    // --- Where it sits -----------------------------------------------------
    // ⚠ THE TWO FIELDS THE MULTI-TRACK TIMELINE IS MADE OF. `start_ms` is
    // NULLABLE and null is not "0": it means "after the last clip on my track",
    // which is how every animatic saved before tracks existed still lays itself
    // out. So it is passed through as null rather than defaulted to a number —
    // writing 0 here would nail every such clip to the head of its row.
    track: f.track ?? 0,
    start_ms: f.start_ms ?? null,
    // The picture's own pan / zoom / fade, and the curves driving them.
    scale: f.scale ?? 1,
    x: f.x ?? 0.5,
    y: f.y ?? 0.5,
    opacity: f.opacity ?? 1,
    keyframes: f.keyframes || {},
    // The source window and read speed — video clips only, harmless elsewhere.
    in_ms: f.in_ms ?? 0,
    out_ms: f.out_ms ?? null,
    speed: f.speed ?? 1,
    color: f.color || "#000000",
    // --- The look ----------------------------------------------------------
    // ⚠ `mask` AND `blend` ARE SENT ONLY WHEN THE CLIP HAS ONE. Both are
    // server-defaulted (`AnimaticMask()` / "normal") and `mask` is NOT optional,
    // so an explicit null would fail validation on a clip that simply has no
    // mask — which is most of them. An absent key takes the default instead.
    effects: f.effects || [],
    ...(f.mask ? { mask: f.mask } : {}),
    ...(f.blend ? { blend: f.blend } : {}),
  };
}
