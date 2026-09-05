// The ✨ AI Editor's floating window resizes from ALL FOUR SIDES and all four
// corners — asked for outright: *"abhi ek taraf se hi chota bara karta hun, mai
// chahta hun charo taraf se"*. This checks the maths that makes that safe.
//
// ⚠ THE BUG THIS EXISTS TO CATCH IS THE LEFT-EDGE WALK. Dragging the left or top
// edge changes the window's POSITION as well as its size, and the obvious
// handler — `x + dx` and `w - dx`, clamped afterwards — keeps moving `x` after
// `w` has already bottomed out at the minimum. The window then stops shrinking
// and slides across the screen instead, dragging its far edge with it. Nothing
// throws; it just behaves wrongly, which is exactly the kind of thing that comes
// back months later. The rule under test is one sentence:
//
//     THE EDGE THE PERSON IS NOT HOLDING MUST NOT MOVE.
//
// ⚠ NO BUNDLER NEEDED. `panel_box.js` imports nothing and wraps every `window`
// read, so plain node gets the fallback viewport and the module loads as-is:
//
//   node tests/panel_resize_check.mjs

import assert from "node:assert";

import {
  MIN_H,
  MIN_W,
  RESIZE_EDGES,
  clampBox,
  resizeBox,
} from "../client/src/animatic/agent/panel_box.js";

const VP = { w: 1280, h: 800 };
const failures = [];

function check(label, ok, detail = "") {
  console.log(`  ${ok ? "ok  " : "FAIL"}   ${label}${ok || !detail ? "" : ` — ${detail}`}`);
  if (!ok) failures.push(label);
}

/** A comfortable window in the middle of the screen — room to grow every way. */
const mid = () => clampBox({ x: 400, y: 200, w: 400, h: 400 }, VP);

const right = (b) => b.x + b.w;
const bottom = (b) => b.y + b.h;

console.log("\n[1] every compass point is wired, and none of them throw\n");
check("eight handles are offered", RESIZE_EDGES.length === 8, RESIZE_EDGES.join(","));
for (const edge of RESIZE_EDGES) {
  let out;
  assert.doesNotThrow(() => {
    out = resizeBox(mid(), 40, 40, edge, VP);
  });
  check(
    `${edge} returns a legal box`,
    out.w >= MIN_W && out.h >= MIN_H && out.x >= 8 && out.y >= 8,
    JSON.stringify(out)
  );
}

console.log("\n[2] the anchored edge holds still\n");
{
  const b = mid();
  const w = resizeBox(b, -60, 0, "w", VP);
  check("dragging the left edge left keeps the right edge put", right(w) === right(b));
  check("…and it got wider", w.w === b.w + 60, `${b.w} → ${w.w}`);
  check("…by moving its left edge", w.x === b.x - 60, `${b.x} → ${w.x}`);

  const n = resizeBox(b, 0, -60, "n", VP);
  check("dragging the top edge up keeps the bottom put", bottom(n) === bottom(b));
  check("…and it got taller", n.h === b.h + 60, `${b.h} → ${n.h}`);

  const e = resizeBox(b, 60, 0, "e", VP);
  check("dragging the right edge keeps x and y put", e.x === b.x && e.y === b.y);
  check("…and only width changed", e.w === b.w + 60 && e.h === b.h);

  const s = resizeBox(b, 0, 60, "s", VP);
  check("dragging the bottom edge only changes height", s.h === b.h + 60 && s.w === b.w);
}

console.log("\n[3] a corner moves both axes at once\n");
{
  const b = mid();
  const nw = resizeBox(b, -40, -40, "nw", VP);
  check("nw grows both ways", nw.w === b.w + 40 && nw.h === b.h + 40);
  check("…with the far corner nailed down", right(nw) === right(b) && bottom(nw) === bottom(b));

  const ne = resizeBox(b, 40, -40, "ne", VP);
  check("ne grows both ways", ne.w === b.w + 40 && ne.h === b.h + 40);
  check("…keeping its left edge and its bottom", ne.x === b.x && bottom(ne) === bottom(b));

  const sw = resizeBox(b, -40, 40, "sw", VP);
  check("sw keeps its right edge and its top", right(sw) === right(b) && sw.y === b.y);

  const se = resizeBox(b, 40, 40, "se", VP);
  check("se keeps the corner it hangs from", se.x === b.x && se.y === b.y);
}

console.log("\n[4] ⚠ THE WALK — a window at its minimum stops, it does not slide\n");
{
  const b = mid();
  // Far more than the window is wide: `w` bottoms out well before this runs out.
  const w = resizeBox(b, 5000, 0, "w", VP);
  check("shrunk to the minimum width", w.w === MIN_W, String(w.w));
  check("…and the right edge NEVER moved", right(w) === right(b), `${right(b)} → ${right(w)}`);

  const n = resizeBox(b, 0, 5000, "n", VP);
  check("shrunk to the minimum height", n.h === MIN_H, String(n.h));
  check("…and the bottom NEVER moved", bottom(n) === bottom(b), `${bottom(b)} → ${bottom(n)}`);

  const nw = resizeBox(b, 5000, 5000, "nw", VP);
  check(
    "the corner bottoms out on both axes at once",
    nw.w === MIN_W && nw.h === MIN_H && right(nw) === right(b) && bottom(nw) === bottom(b)
  );
}

console.log("\n[5] and it cannot be dragged off the screen\n");
{
  const b = mid();
  const w = resizeBox(b, -5000, 0, "w", VP);
  check("growing left stops at the margin", w.x === 8, String(w.x));
  check("…and still keeps the right edge", right(w) === right(b));

  const n = resizeBox(b, 0, -5000, "n", VP);
  check("growing up stops at the margin", n.y === 8, String(n.y));

  const e = resizeBox(b, 5000, 0, "e", VP);
  check("growing right stops inside the viewport", right(e) <= VP.w - 8, String(right(e)));

  const s = resizeBox(b, 0, 5000, "s", VP);
  check("growing down stops inside the viewport", bottom(s) <= VP.h - 8, String(bottom(s)));
}

console.log("\n[6] a viewport smaller than the minimum is still survivable\n");
{
  const tiny = { w: 320, h: 280 };
  for (const edge of RESIZE_EDGES) {
    let out;
    assert.doesNotThrow(() => {
      out = resizeBox({ x: 10, y: 10, w: 300, h: 260 }, -400, -400, edge, tiny);
    });
    check(
      `${edge} survives a viewport with no room`,
      Number.isFinite(out.x) && Number.isFinite(out.y) && out.w >= MIN_W && out.h >= MIN_H,
      JSON.stringify(out)
    );
  }
}

console.log();
if (failures.length) {
  console.log(`FAILED (${failures.length}): ${failures.join(", ")}`);
  process.exit(1);
}
console.log("✓ the floating window resizes from every side, and the far edge stays put.");
