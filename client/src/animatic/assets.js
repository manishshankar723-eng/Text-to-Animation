// assets.js — THE MEDIA LIBRARY. What you HAVE, as opposed to where you put it.
//
// ⚠ THE LIBRARY AND THE TIMELINE ARE TWO LISTS, and this module is the whole of
// that separation. The Media pane used to BE the timeline — it listed `frames`
// grouped by where each clip came from — so deleting a clip deleted the only
// record that the file had ever been added, and the only way back was to upload
// it again. Reported as:
//
//     "when i upload/generate Veo video and then i delete in time so i see in
//      media panel also delete … i want when user delete video, storboard image,
//      veo video, audio and shapes in timeline after upload in media so only clip
//      delete in timeline not delete in media panel i want stay in media panel so
//      user need deleetd cipl again so user go media panle and drang and drop in
//      perticular layer"
//
// So: an ASSET is a source. A clip is a placement of one. Deleting a clip leaves
// the asset; dragging the asset out makes a new clip; the asset goes only when
// its own ✕ is pressed.
//
// ⚠ AND IT IS PURE, with no React import, for the same reason `frame_save.js` and
// `scene.js` are: `tests/asset_fields_check.py` loads it under node and compares
// `assetForSave` against `AnimaticAsset` in `server/schemas.py`. The logic that
// has a right answer lives where a test can reach it.

/**
 * WHAT EACH KIND OF SHAPE LOOKS LIKE, and why there are only four.
 *
 * `image` / `video` / `color` are the three things a picture clip can be —
 * exactly `AnimaticFrame.kind` — and `audio` is a sound file, which becomes an
 * audio track rather than a picture clip. Text and shapes are deliberately NOT
 * here: they have no source to keep. A caption is typed and a shape is picked
 * from the Shapes tab, which is already a library that a deletion cannot empty,
 * so the report's "and shapes" is answered by the gallery that was always there.
 */
export const ASSET_KINDS = ["image", "video", "color", "audio"];

/**
 * ONE ASSET'S IDENTITY — what makes two additions the same source.
 *
 * ⚠ NOT THE ID. Ids are minted per row; this is the thing behind the row, and it
 * is what stops the library growing a duplicate card every time the same panel is
 * dragged out twice or the same board is imported again. Keyed by what the server
 * resolves a picture FROM, so two cards can only collide when they really would
 * serve the same bytes.
 *
 * ⚠ TWIN of the identity `_asset_url` resolves on the server. If you add a source
 * kind, both have to learn it.
 */
export function assetKey(asset) {
  if (!asset) return "";
  const kind = asset.kind || "image";
  if (kind === "audio") return `audio:${asset.upload_id || ""}`;
  if (kind === "color") return `color:${(asset.color || "#000000").toLowerCase()}`;
  const src = asset.src || {};
  if (src.kind === "pose") {
    return `pose:${src.storyboard_id || ""}:${src.index ?? ""}:${src.frame ?? ""}`;
  }
  if (src.kind === "panel") return `panel:${src.storyboard_id || ""}:${src.index ?? ""}`;
  // A still and a piece of footage are stored under one id space, so the kind has
  // to be in the key: `img_<id>.png` and `vid_<id>.mp4` are different files.
  return `${src.kind || "upload"}:${src.upload_id || ""}`;
}

/**
 * WHICH SECTION OF THE MEDIA PANE A CARD BELONGS IN.
 *
 * ⚠ BY WHERE IT CAME FROM, NOT BY WHAT IT IS — the same rule `frameOrigin` uses,
 * and the same reason: a board shot that has been through Veo is a video FILE
 * now, and it has to stay in Storyboard Frames where the user left it rather than
 * moving to Video the moment it is animated.
 */
export function assetOrigin(asset) {
  if ((asset?.kind || "image") === "audio") return "audio";
  // ⚠ A KEY POSE GETS A SECTION OF ITS OWN, and it is not a nicety. ✨ Animatic
  // images adds a card per DRAWING — sixteen for one four-second shot — so on a
  // board of any size they would bury the panels they were made from inside
  // Storyboard Frames, which is a section people keep folded shut. Reported the
  // moment the cards existed at all: "media panel mai generted iamge nhi dikh
  // rah ahai". A named section is the difference between "they are in there
  // somewhere" and "there they are".
  //
  // ⚠ A VEO TAKE OF A POSE IS NOT ONE. `attachVeoClip` keeps the pose's `src`
  // underneath the video source, so such a card still carries `frame` — but its
  // `src.kind` is "video" by then, and it belongs with the renders in Storyboard
  // Frames exactly as a take of a panel does. Same ordering rule as
  // `cardRowKind`: what it IS now wins over what it was made from.
  if (asset?.src?.kind === "pose" && asset?.src?.storyboard_id) return "poses";
  if (asset?.src?.storyboard_id) return "board";
  if ((asset?.kind || "image") === "video") return "video";
  return "image";
}

/**
 * DID THIS CARD COME OUT OF A STORYBOARD? — a panel, a key pose, or a Veo take
 * of either.
 *
 * ⚠ IT EXISTS BECAUSE "which section is it filed under" AND "did it come off a
 * board" STOPPED BEING THE SAME QUESTION the moment key poses got a section of
 * their own. Four places asked `assetOrigin(card) === "board"` to mean the
 * second — the drag's `x-anim-board` marker, the ＋ on a card, the drop rule and
 * the overlay routing — and every one of them would have quietly started
 * answering "no" for a key pose, which routes a board picture onto the overlay
 * Images lane. This is the question those four actually meant.
 */
export const isBoardAsset = (asset) => {
  const origin = assetOrigin(asset);
  return origin === "board" || origin === "poses";
};

/**
 * Serve path for one card's picture, or "" when it has none (a colour card).
 *
 * ⚠ EVERY SHAPE HERE IS RESOLVABLE WITHOUT A SAVE — an upload by its upload id, a
 * panel by (board, index). That is the point, and it is why the library does not
 * repeat the bug the storyboard import hit: `AnimaticFrame.url` resolves an id
 * through the SAVED frame list, so a url handed out before the autosave lands can
 * only 404. A library card is servable the instant it is added.
 *
 * ⚠ TWIN of `_asset_url` in `server/animatics.py`, which fills the same field on
 * read. The two must produce the same path or a reload re-fetches every blob.
 * (It may legitimately produce a LONGER one: the server appends `?v=<mtime>` to a
 * panel so a redraw is picked up, which this cannot know. A url that moves is
 * re-fetched, which is correct.)
 */
export function assetUrl(animaticId, asset) {
  const kind = asset?.kind || "image";
  if (!animaticId || !asset) return "";
  if (kind === "color") return "";
  if (kind === "audio") {
    return asset.upload_id ? `/animatics/${animaticId}/media/${asset.upload_id}` : "";
  }
  const src = asset.src || {};
  if (src.kind === "video") {
    // A VIDEO WANTS A STILL, not the MP4 — an <img> can only fail to draw one.
    // Same `?poster=1` the timeline's own video clips use.
    return src.upload_id
      ? `/animatics/${animaticId}/media/${src.upload_id}?poster=1`
      : "";
  }
  if (src.kind === "upload") {
    return src.upload_id ? `/animatics/${animaticId}/media/${src.upload_id}` : "";
  }
  if ((src.kind === "panel" || src.kind === "pose") && src.storyboard_id && src.index != null) {
    const pose = src.kind === "pose" && src.frame != null ? `?frame=${src.frame}` : "";
    return `/animatics/${animaticId}/panel/${src.storyboard_id}/${src.index}${pose}`;
  }
  return "";
}

/**
 * A library card FROM A PICTURE CLIP — used when something is added to the
 * timeline, and to backfill the library of a project saved before it existed.
 *
 * ⚠ THE TIMING IS DROPPED ON PURPOSE. `start_ms`, `track`, `in_ms`, `effects`,
 * `keyframes` — all of it is where the clip PLAYS, none of it is what the source
 * IS. Keeping any of it would give the library a second, competing opinion about
 * the cut. What survives is `duration_ms`, re-read as the source's natural length
 * so a new clip made from the card opens at the right size.
 */
export function assetFromFrame(frame, id) {
  const kind = frame?.kind || "image";
  // For a video the NATURAL length is the source window, not the hold — a 54s
  // take trimmed to 3s on the timeline is still a 54s take in the library.
  const natural =
    kind === "video" && frame?.out_ms != null
      ? Math.max(0, Math.round(Number(frame.out_ms)))
      : Math.max(0, Math.round(Number(frame?.duration_ms) || 0));
  return {
    id: id || frame?.id || "",
    kind,
    src: { ...(frame?.src || {}) },
    upload_id: "",
    label: frame?.label || "",
    duration_ms: natural,
    color: frame?.color || "#000000",
  };
}

/**
 * WHICH SOURCE AN OVERLAY IS PLAYING — an `AnimaticFrameSource`-shaped object.
 *
 * ⚠ AN OVERLAY IS A PICTURE CLIP THAT DOES NOT LIVE IN `frames`, and until it
 * carried a `src` the library could not recognise one at all: the ×N badge
 * under-counted, the card's ✕ left it playing from a source no longer listed,
 * and "Select its clips" could not find it. `AnimaticOverlay.src` is what fixed
 * that; this is the function that reads it safely.
 *
 * ⚠ AND THE FALLBACK IS THE HALF THAT DOES THE WORK. Three things arrive with no
 * usable `src`, and all three are honestly `{kind: "upload", upload_id}`:
 *   · an overlay saved BEFORE the field existed — it gets the schema default,
 *     which is `kind: "panel"` with no ids. ⚠ Keying on that raw would give every
 *     legacy overlay in the project the SAME key (`panel::`) and fold them into
 *     one card.
 *   · a dropped file (`addOverlayFiles`) and a ✨ generated picture, whose cards
 *     are minted from the very upload id the overlay carries. Those matched on
 *     `upload_id` all along; `src` merely records what was already true.
 * Only a BOARD PANEL needs the stored `src`, because its `upload_id` is a COPY
 * of the panel's bytes made by `overlayFromFrame` and points at nothing the
 * library knows.
 */
export function overlaySource(overlay) {
  const src = overlay?.src || {};
  const kind = src.kind || "";
  const usable =
    kind === "panel" || kind === "pose"
      ? Boolean(src.storyboard_id)
      : kind === "upload" || kind === "video"
        ? Boolean(src.upload_id)
        : false;
  if (usable) return { ...src };
  return { kind: "upload", upload_id: overlay?.upload_id || "" };
}

/**
 * A library card FROM AN OVERLAY — a picture on an Images lane.
 *
 * ⚠ NO LABEL, and that is not an omission. `AnimaticOverlay` has no name field:
 * the timeline draws every overlay bar as the literal word "Picture". So a card
 * derived from one is unnamed, and `mergeAssets` is what makes that harmless —
 * an overlay made from a panel or an upload keys to the card that already
 * exists, WITH its name, and only a genuinely orphaned overlay yields a card
 * captioned "Untitled".
 */
export function assetFromOverlay(overlay, id) {
  return {
    id: id || overlay?.id || "",
    kind: "image",
    src: overlaySource(overlay),
    upload_id: "",
    label: "",
    duration_ms: Math.max(0, Math.round(Number(overlay?.duration_ms) || 0)),
    color: "#000000",
  };
}

/** A library card FROM AN AUDIO CLIP. Its file, its name, its length. */
export function assetFromAudio(track, id) {
  return {
    id: id || track?.id || track?.upload_id || "",
    kind: "audio",
    src: { kind: "upload" },
    upload_id: track?.upload_id || "",
    label: track?.filename || track?.label || "",
    // `AnimaticAudio.duration_ms` is the FILE's length, not the clip's play time
    // (that is `trim_ms`) — so a track razored into four pieces still gives one
    // library card that knows how long the whole recording is.
    duration_ms: Math.max(0, Math.round(Number(track?.duration_ms) || 0)),
    color: "#000000",
    // Empty for a file the user uploaded — they own it, nobody has to be
    // credited. A sound taken from the Sounds tab arrives with the line already
    // written (`freesound.credit_line`), and this is where it lands so that it
    // outlives the search that found it.
    attribution: track?.attribution || "",
  };
}

/**
 * ADD to a library, skipping anything already in it. Returns a NEW list.
 *
 * Deduped by `assetKey`, so importing the same board twice, or dragging a card
 * out and deleting the clip, or a reload that backfills — none of them can grow a
 * second row for one source.
 */
export function mergeAssets(library, incoming) {
  const out = Array.isArray(library) ? [...library] : [];
  const seen = new Set(out.map(assetKey));
  for (const asset of incoming || []) {
    if (!asset) continue;
    const key = assetKey(asset);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(asset);
  }
  return out;
}

/**
 * THE LIBRARY A PROJECT IMPLIES, for one saved before there was a library.
 *
 * ⚠ CALLED ONLY WHEN `assets` IS ABSENT, NEVER WHEN IT IS EMPTY. An empty list
 * means the user emptied the library on purpose — they pressed ✕ on the last card
 * — and re-deriving one from the timeline would put every card straight back, so
 * the ✕ would look broken. `undefined` is "this project is older than the
 * library"; `[]` is "there is nothing in it". The server passes the missing key
 * through as `[]` and cannot tell them apart; the client can, which is why this
 * lives here. See `_assets_of` in server/animatics.py.
 *
 * @param mintId a fresh id per card, so a library row and the clip it was
 *               derived from never share an id — two lists, two id spaces.
 */
export function libraryFromProject({ frames, overlays, audioTracks }, mintId) {
  const mint = typeof mintId === "function" ? mintId : () => "";
  const cards = [];
  // ⚠ COLOUR CARDS INCLUDED, and the reason is consistency rather than value.
  // `addColorCard` puts one in the library when you make it, so leaving them out
  // HERE would mean a blackout you made today is listed and one you made last
  // week is not — the pane would be missing something that is plainly on the
  // timeline in front of you. `assetKey` keys them by hex, so four blackouts are
  // one card rather than four.
  for (const frame of frames || []) cards.push(assetFromFrame(frame, mint()));
  // ⚠ AND THE PICTURES ON THE IMAGES LANES, which are `overlays` and not
  // `frames`. Leaving them out is how a project whose only picture sits on an
  // Images lane derived an EMPTY library and opened with a Media pane that said
  // "nothing here yet" over a timeline plainly holding a picture.
  // ⚠ AFTER the frames, deliberately: `mergeAssets` keeps the FIRST card for a
  // key, and a frame's card carries a label where an overlay's cannot.
  for (const overlay of overlays || []) {
    if (!overlay?.upload_id && !overlay?.src) continue;
    cards.push(assetFromOverlay(overlay, mint()));
  }
  for (const track of audioTracks || []) {
    if (!track?.upload_id) continue;
    cards.push(assetFromAudio(track, mint()));
  }
  return mergeAssets([], cards);
}

/**
 * A NEW PICTURE CLIP from a library card — what a drag out of Media makes.
 *
 * ⚠ IT IS A NEW CLIP EVERY TIME, sharing only the source. Drag one card onto
 * three rows and you have three clips that trim, grade and move independently,
 * which is what a library is FOR. Nothing here is read back off the card
 * afterwards: an asset has no timing to keep in step with.
 *
 * `defaultMs` is the hold to use when the source has no natural length of its own
 * (a still, or a file the server could not measure).
 */
export function clipFromAsset(asset, { id, animaticId, defaultMs = 2000 } = {}) {
  const kind = asset?.kind || "image";
  const natural = Math.max(0, Math.round(Number(asset?.duration_ms) || 0));
  const url = assetUrl(animaticId, asset);
  const clip = {
    id,
    src: { ...(asset?.src || {}) },
    kind,
    // A still holds for the default; footage opens at its full natural length,
    // which is what "I dropped a 6-second take in" should mean.
    duration_ms: kind === "video" && natural ? natural : defaultMs,
    label: asset?.label || "",
    in_ms: 0,
    out_ms: kind === "video" && natural ? natural : null,
    speed: 1,
    scale: 1,
    x: 0.5,
    y: 0.5,
    opacity: 1,
    keyframes: {},
    color: asset?.color || "#000000",
  };
  // ⚠ A CLIP WITH A FILE BEHIND IT MUST CARRY A `url`, or the thumbnail effect
  // never fetches it and both the Media card and the monitor show nothing. That
  // has been the same bug twice (`newVideoClip`, then `attachVeoClip`) — see the
  // ⚠ note on `newVideoClip` in AnimaticEditor.jsx.
  if (url) clip.url = url;
  return clip;
}

/**
 * EXACTLY WHAT A LIBRARY CARD LOOKS LIKE WHEN IT IS SAVED.
 *
 * ⚠ SAME SHAPE THE SAVE SENDS AND THE DIRTY-CHECK COMPARES, and it is a
 * WHITELIST — the same trap `frameForSave` fell into twice. A field the schema
 * gains and this function does not mention is computed by the editor and then
 * thrown away on the way to the server, with no error anywhere, because dropping
 * a key is not a failure. `tests/asset_fields_check.py` compares the two and
 * fails on the next one that goes missing.
 *
 * `url` is deliberately absent: the server fills it on read and ignores it on
 * write, so sending it back would store a path that goes stale.
 */
export function assetForSave(asset) {
  return {
    id: asset.id,
    kind: ASSET_KINDS.includes(asset.kind) ? asset.kind : "image",
    src: asset.src || { kind: "upload" },
    upload_id: asset.upload_id || "",
    label: asset.label || "",
    duration_ms: Math.max(0, Math.round(Number(asset.duration_ms) || 0)),
    color: asset.color || "#000000",
    // The credit a Freesound import arrived with, "" for everything else. ⚠ IT
    // HAS TO BE ON THIS LIST: a CC BY sound obliges whoever publishes the video
    // to name its author, and a field this whitelist forgets is a field the
    // server never sees — the exact silent loss this file's test exists to
    // catch. See `AnimaticAsset.attribution`.
    attribution: asset.attribution || "",
  };
}
