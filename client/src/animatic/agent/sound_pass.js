// sound_pass.js — PHASE D AND PHASE E: the sound effects, and the score.
//
// ---------------------------------------------------------------------------
// ⚠ THESE TWO RUN LAST, AND THAT IS THE OPPOSITE OF THE OTHER TWO PASSES.
// ---------------------------------------------------------------------------
// Phase B (the voiceover) and phase C (the Veo take) both run BEFORE the steps,
// because both MOVE THE PICTURES — a shot stretched to cover its line or its
// footage invalidates every timing decision in the plan, which is why there is a
// re-anchor between them and the run (see `voice_pass.js`).
//
// A sound effect is the other way round. It is placed AT A MOMENT — "the door
// slams as shot 9 begins" — so the only thing it needs is where shot 9 actually
// starts, and that is not knowable until the last step has run: the plan itself
// re-times shots (`set_shot_duration`, `set_all_durations`), so a whoosh laid
// down before the steps would be a whoosh on the wrong frame.
//
//     speaking → rendering → anchoring → running → SCORING → done
//     └── move the pictures ──┘  └ edit ┘   └ lay the sound on the finished film
//
// So this is the one pass that reads the film rather than changing it, and
// because of that it needs no re-anchor of its own: nothing downstream of it
// makes a timing decision. `scoring` is the last thing the 🎬 button does.
//
// ---------------------------------------------------------------------------
// ⚠ THE WORDS COME FROM THE ANALYSE CALL, WHICH WAS ALREADY PAID FOR.
// ---------------------------------------------------------------------------
// `director.py`'s reading returns `sfx` per shot and one `music` cue for the
// film, for exactly the reason it already returns `motion` and `dialogue`: what
// a moment should SOUND like is a story decision, and asking a second model at
// placement time would be asking a model that has not read the film. So the
// cues cost nothing extra, they are on screen in the preview before Run is
// pressed, and this file never calls anything.
//
// ---------------------------------------------------------------------------
// ⚠ A CUE IS A SEARCH TERM, NOT A SOUND. And the search is FENCED TO CC0.
// ---------------------------------------------------------------------------
// The model writes "heavy wooden door slams shut"; the server searches Freesound
// for it and files the best match in as an ordinary audio upload — the same
// `audio_<id>.mp3` the Sounds tab imports and a dropped mp3 uploads to (see
// `POST /animatics/{id}/soundtrack`).
//
// ⚠ AND IT IS CC0 ONLY, WHICH IS NOT THE SAME FENCE THE SOUNDS TAB USES. The tab
// offers CC BY as well, because a person choosing a sound by hand can read the
// "credit needed" badge and decide. NOBODY IS READING A BADGE HERE — this pass
// puts eleven sounds on a timeline while the user watches a progress line — and a
// CC BY sound placed automatically is an attribution obligation the customer
// never agreed to and will not discover until somebody complains about their
// published video. So an automatic pass takes only the licence that obliges
// nobody. The fence is the SERVER's (`licence="safe"`); this note is why.
//
// ---------------------------------------------------------------------------
// ⚠ MUSIC IS LAID AS ORDINARY CLIPS, AND A LOOP IS SEVERAL OF THEM.
// ---------------------------------------------------------------------------
// A Freesound preview is 10-30 seconds and a film is minutes, so the bed has to
// repeat. There is no "loop" flag on an audio clip and there must not be one: the
// razor, the waveform, the mixer, the fades and the EXPORTER all already
// understand "several clips of one file, back to back" — that is precisely what a
// razored take is — whereas a loop flag would be a second thing every one of them
// has to learn. So a 90-second film over a 20-second bed is five clips of one
// upload, and the last one is trimmed to land on the final frame.
//
// ⚠ IT IS ONE FILE, THEREFORE ONE ENTRY AGAINST THE AUDIO CAP. The cap counts
// FILES, not clips (`_audio_files_of`, and `audioFileCount` in the editor), which
// is the same reason razoring a track four times does not use up four tracks.
//
// ---------------------------------------------------------------------------
// ⚠ IT IS PURE. No React, no fetch, no editor import.
// ---------------------------------------------------------------------------
// Same rule as the other five modules here: `tests/director_sound_check.py`
// imports this under node and drives every placement with no browser, no backend
// and no Freesound key. The runner owns the async — one server call — and this
// owns every decision that call's result leads to.

/** The include flags phases D and E answer to. Both are in `INCLUDE_KEYS`. */
export const SFX_KEY = "sfx";
export const MUSIC_KEY = "music";

/**
 * HOW MANY DISTINCT SOUNDS ONE RUN MAY FETCH.
 *
 * ⚠ IT IS A BUDGET, NOT A TASTE RULE, and the budget is somebody else's. A free
 * Freesound key allows 60 requests a MINUTE for the whole deployment, and each
 * distinct cue costs two of them (one search, one metadata read before the CDN
 * download). Ten cues is twenty requests — one run, comfortably inside the
 * minute, leaving room for the person browsing the Sounds tab in the next tab
 * along. A 60-shot board with a cue on every shot would spend the entire
 * deployment's budget on one press.
 *
 * ⚠ AND IT IS DISTINCT SOUNDS, NOT CLIPS. Six shots that all cue "footsteps on
 * gravel" are one search, one download, one file and six clips — which is also
 * the right FILM: one recording of gravel is a place, six different ones is six
 * places.
 */
export const MAX_SFX_SOUNDS = 10;

/** And how many places they may be laid down. A clip is free; a fetch is not. */
export const MAX_SFX_CLIPS = 32;

/**
 * HOW LOUD, AND THE MUSIC'S TWO ANSWERS.
 *
 * ⚠ THE BED'S LEVEL DEPENDS ON WHETHER ANYONE IS TALKING, and it is set here
 * rather than left at 1.0 for the user to discover. Music at full level under a
 * voiceover does not sound like music, it sounds like a fault: the words stop
 * being audible and the first thing anybody does is drag the level down. There is
 * a real ducking graph in `audio_engine.js` for the PREVIEW, but the EXPORT mixes
 * what the clips say (`animatic.py`), so a level that only exists in the browser
 * would be a preview that lies about the mp4. A number on the clip is true in
 * both.
 *
 * ⚠ SFX SIT UNDER THE DIALOGUE TOO, but only a little: a door slam is a story
 * event and burying it defeats the cue.
 */
export const SFX_VOLUME = 0.62;
export const MUSIC_VOLUME_UNDER_SPEECH = 0.14;
export const MUSIC_VOLUME_ALONE = 0.3;

/** The bed comes up and goes away. Long enough to be a fade, not a swell. */
export const MUSIC_FADE_IN_MS = 2000;
export const MUSIC_FADE_OUT_MS = 2500;

/** A sound effect's own edges — short, so the cue still lands on its frame. */
export const SFX_FADE_MS = 40;

/**
 * THE LENGTH BOUNDS, AND THEY ARE PREFERENCES RATHER THAN REQUIREMENTS.
 *
 * ⚠ BOTH WERE FAR TIGHTER AND BOTH HELPED MAKE A FILM SILENT. 8 seconds for an
 * effect and a 12-second FLOOR for the bed, on top of a CC0-only filter and a
 * four-word query, left "light feather rustle" and "ambient peaceful piano
 * underscore" with zero results each — a plan that promised two effects and a
 * score delivered one effect and no music. Reported from the screen.
 *
 * ⚠ AND NOTHING DOWNSTREAM NEEDS EITHER BOUND. A bed shorter than the film is
 * LOOPED (`musicPlacement`), and an effect longer than its shot is clamped to the
 * end of the film by `trackPlayMs` — so these only exist to keep a five-minute
 * field recording from being filed as a door slam, and a one-second blip from
 * being filed as a score. Wide enough to be true, not narrow enough to be a
 * filter. The server drops them ENTIRELY on its second attempt; see
 * `_cue_attempts` in `server/animatics.py`.
 */
export const SFX_MAX_SECONDS = 30;
export const MUSIC_MIN_SECONDS = 5;

/** What the two lanes are called on the timeline. The user renames them freely. */
export const SFX_LANE_NAME = "Sound FX";
export const MUSIC_LANE_NAME = "Music";

/**
 * A cue's KEY — what makes two cues the same sound.
 *
 * ⚠ NORMALISED, because the model writes "Door slams" for shot 4 and "door slam"
 * for shot 11 and means one recording both times. Two searches for that would
 * cost two requests out of a shared budget and would land two different doors in
 * one house.
 */
export function cueKey(query) {
  return String(query || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

/**
 * WHAT PHASE D WOULD LAY DOWN — free, and it calls nothing.
 *
 * @param analysis the reading from the analyse call, or null
 * @param frames   the picture row, for turning a shot number into a clip id
 * @param starts   where each frame begins, the same array `readCtx` carries
 * @returns {{ cues, sounds, skipped }}
 *          `cues`    one per PLACE a sound goes — `{shot, frame_id, key, query, at_ms, hold_ms}`
 *          `sounds`  one per DISTINCT sound to fetch — `{key, query, kind}`
 *          `skipped` `[{shot, query, why}]`, printed verbatim in the preview
 *
 * ⚠ THE RULES PLANNER REACHES THIS WITH NOTHING AND THAT IS THE HONEST ANSWER.
 * "Just the rhythm" writes no words — no captions, no motion prompts, no cues —
 * so handed no analysis it returns nothing and `sfxDue` says why. Inventing
 * "whoosh" for every cut out of arithmetic would be the rules planner having an
 * opinion about the story, which is the one thing it does not have.
 */
export function sfxCues({ analysis, frames, starts } = {}) {
  const row = frames || [];
  const at = starts || [];
  const cues = [];
  const skipped = [];
  const sounds = new Map();

  for (const shot of (analysis && analysis.shots) || []) {
    const query = String((shot && shot.sfx) || "").trim();
    if (!query) continue;
    const number = Number(shot.shot);
    const frame = row[number - 1];
    if (!frame) {
      skipped.push({ shot: shot.shot, query, why: `there is no shot ${shot.shot} to sound` });
      continue;
    }
    const key = cueKey(query);
    if (!key) continue;
    // ⚠ THE BUDGET IS SPENT ON DISTINCT SOUNDS, so a repeat of one already in
    // the list is free and is never refused. Only a NEW sound can hit the cap.
    if (!sounds.has(key) && sounds.size >= MAX_SFX_SOUNDS) {
      skipped.push({
        shot: shot.shot,
        query,
        why:
          `this run already fetches ${MAX_SFX_SOUNDS} different sounds, which is all ` +
          "the shared sound-library budget allows in one pass",
      });
      continue;
    }
    if (cues.length >= MAX_SFX_CLIPS) {
      skipped.push({ shot: shot.shot, query, why: `past the ${MAX_SFX_CLIPS}-cue ceiling` });
      continue;
    }
    if (!sounds.has(key)) sounds.set(key, { key, query, kind: "sfx" });
    cues.push({
      shot: number,
      frame_id: frame.id,
      key,
      // The model's own wording is kept beside the key, because the key is
      // punctuation-stripped and the preview shows the user what was ASKED FOR.
      query,
      at_ms: Math.max(0, Math.round(Number(at[number - 1]) || 0)),
      hold_ms: Math.max(0, Math.round(Number(frame.duration_ms) || 0)),
    });
  }

  return { cues, sounds: [...sounds.values()], skipped };
}

/**
 * WHAT PHASE E WOULD LAY DOWN — one bed for the whole film, or nothing.
 *
 * ⚠ ONE TRACK, NEVER A SCORE PER SCENE. A change of music is the strongest
 * punctuation a film has and it belongs to a person, not to a pass that runs
 * while they watch a progress line. The reading says what the film sounds like
 * once; if the user wants a second cue they drop one in from the Sounds tab,
 * which is a gesture that already exists.
 *
 * @returns `{ key, query, mood, kind }` or null
 */
export function musicCue({ analysis } = {}) {
  const music = (analysis && analysis.music) || null;
  const query = String((music && music.query) || "").trim();
  if (!query) return null;
  const key = cueKey(query);
  if (!key) return null;
  return { key, query, mood: String((music && music.mood) || "").trim(), kind: "music" };
}

/**
 * THE BED A FILM GETS WHEN NOBODY READ IT — phase E's answer on the free door.
 *
 * ⚠ THIS FILE ARGUED AGAINST EXACTLY THIS, AND THE ARGUMENT WAS TOO WIDE. The
 * note over `sfxCues` says the rules planner "writes no words — no captions, no
 * motion prompts, no cues", and that is right about a DOOR SLAM: which moment in
 * a film is a door closing is a story question and arithmetic cannot answer it.
 * It is not right about a music bed. A bed is not an event in the film, it is a
 * property of the whole of it, and the one thing a timeline knows without being
 * told — how fast it cuts — is the same thing an editor uses to choose one.
 *
 * Reported from the screen: the AI pass failed with a 403, the run fell back to
 * "Just the rhythm", and Background music was ticked — so the panel showed a
 * ticked box that could not do anything. *"Sound and bg music nhi aaya timeline
 * pe"*.
 *
 * ⚠ THE QUERY IS READ OFF THE CUTTING PACE, which is the only evidence there is.
 * A film that cuts every two seconds is not asking for the same bed as one that
 * holds every shot for eight. Three bands rather than a curve, because the
 * search is a bag of words and "3.4-second-median music" is not a thing anyone
 * has uploaded.
 *
 * ⚠ AND IT IS INSTRUMENTAL, EVERY TIME. A bed with a vocal on it fights the
 * voiceover for the same part of the ear, and phase B may add one after this cue
 * was written. The word is in the query rather than in a filter because
 * Freesound has no such filter — see `_cue_attempts` in `server/animatics.py`.
 *
 * ⚠ IT IS ONLY EVER A FALLBACK. A reading that named its own music wins: it read
 * the film and this counted its cuts. See `loadCues` in `useDirectorRun.js`.
 *
 * @returns `{ key, query, mood, kind }` — the same shape `musicCue` returns
 */
export function houseMusicCue({ frames } = {}) {
  const row = frames || [];
  if (!row.length) return null;
  const lengths = row
    .map((f) => Math.max(1, Number(f?.duration_ms) || 0))
    .sort((a, b) => a - b);
  const mid = Math.floor(lengths.length / 2);
  const median =
    lengths.length % 2 ? lengths[mid] : Math.round((lengths[mid - 1] + lengths[mid]) / 2);
  const band =
    median <= 2500
      ? { mood: "upbeat", query: "upbeat energetic corporate background music instrumental" }
      : median <= 5000
        ? { mood: "warm", query: "calm cinematic background music instrumental" }
        : { mood: "ambient", query: "slow ambient atmospheric background music instrumental" };
  const key = cueKey(band.query);
  if (!key) return null;
  return { key, query: band.query, mood: band.mood, kind: "music" };
}

/** What a house sound effect is. One recording, laid on the cuts that were treated. */
export const HOUSE_SFX_QUERY = "whoosh transition swoosh";

/**
 * THE EFFECTS A FILM GETS WHEN NOBODY READ IT — phase D's answer on the free door.
 *
 * ⚠ AND IT IS NOT "A WHOOSH ON EVERY CUT", which is what `sfxCues`' note refuses
 * and is still refused here. It is a whoosh on the cuts THE PLAN ALREADY
 * TREATED — the ones that carry a dissolve, a slide or a wipe. That distinction
 * is the whole justification: laying a sound on an ordinary cut is an opinion
 * about the story ("something happens here"), while laying one under a
 * transition is an opinion about the TRANSITION, and rhythm is the one thing
 * this planner is allowed to have opinions about. It is also what an editor
 * does — a swoosh under a wipe is craft, not narrative.
 *
 * ⚠ ONE RECORDING, NOT ONE PER CUT. Eleven different whooshes is eleven searches
 * out of a shared budget and eleven different noises in one film; one is a
 * choice. Same reasoning as `sfxCues`' "six shots that all cue footsteps on
 * gravel are one search".
 *
 * ⚠ IT LANDS WITH THE TRANSITION, NOT ON THE CUT. A transition is
 * boundary-local — half of it plays before the cut and half after (see
 * `transitions.js`) — so a sound started ON the cut arrives halfway through the
 * gesture it is supposed to be sounding.
 *
 * ⚠ IT IS ONLY EVER A FALLBACK, like `houseMusicCue`: a reading that cued its own
 * effects wins.
 *
 * @param plan   the plan being previewed — only its `add_transition` steps are read
 * @param frames the picture row, for turning a cut into the arriving clip
 * @param starts where each frame begins
 * @returns the same `{ cues, sounds, skipped }` shape `sfxCues` returns
 */
export function houseSfxCues({ plan, frames, starts } = {}) {
  const row = frames || [];
  const at = starts || [];
  const cues = [];
  const skipped = [];
  const steps = (plan && Array.isArray(plan.steps) ? plan.steps : []).filter(
    (step) => step && step.verb === "add_transition"
  );
  if (!steps.length || !row.length) return { cues, sounds: [], skipped };
  const key = cueKey(HOUSE_SFX_QUERY);
  if (!key) return { cues, sounds: [], skipped };

  for (const step of steps) {
    const cut = Number(step.args && step.args.cut);
    // The ARRIVING shot: cut 1 is between shot 1 and shot 2, so the sound belongs
    // to `frames[cut]`. A cut naming a shot that is no longer there is skipped in
    // silence — the transition step itself is already refused by `validatePlan`.
    const frame = row[cut];
    if (!Number.isFinite(cut) || !frame) continue;
    if (cues.length >= MAX_SFX_CLIPS) {
      skipped.push({
        shot: cut + 1,
        query: HOUSE_SFX_QUERY,
        why: `past the ${MAX_SFX_CLIPS}-cue ceiling`,
      });
      continue;
    }
    const ms = Math.max(0, Math.round(Number(step.args && step.args.ms) || 0));
    const start = Math.max(0, Math.round(Number(at[cut]) || 0));
    cues.push({
      shot: cut + 1,
      frame_id: frame.id,
      key,
      query: HOUSE_SFX_QUERY,
      at_ms: Math.max(0, start - Math.round(ms / 2)),
      hold_ms: Math.max(0, Math.round(Number(frame.duration_ms) || 0)),
    });
  }

  if (!cues.length) return { cues: [], sounds: [], skipped };
  return { cues, sounds: [{ key, query: HOUSE_SFX_QUERY, kind: "sfx" }], skipped };
}

/**
 * IS THERE A PASS TO RUN AT ALL?
 *
 * Returns a reason rather than a boolean, for the same reason `speechDue` does:
 * each way of answering "no" is a different thing to tell the user, and the panel
 * prints it verbatim under the tick box.
 */
export function sfxDue(include, sounds) {
  if (include && include[SFX_KEY] === false) {
    return { due: false, why: "Sound effects are switched off for this run." };
  }
  if (!sounds || !sounds.length) {
    return {
      due: false,
      why:
        "Nothing cued any sound effects. “Just the rhythm” lays one under each cut it " +
        "treats, so a plan with no transitions in it has nowhere to put a sound — " +
        "ask the AI to read the film for effects that belong to the story.",
    };
  }
  return { due: true, why: "" };
}

export function musicDue(include, cue) {
  if (include && include[MUSIC_KEY] === false) {
    return { due: false, why: "Background music is switched off for this run." };
  }
  if (!cue) {
    return {
      due: false,
      why: "There is nothing on the timeline to score yet.",
    };
  }
  return { due: true, why: "" };
}

/**
 * THE ONE REQUEST BOTH PASSES MAKE. Distinct sounds, once each.
 *
 * ⚠ ONE CALL, NOT ONE PER CUE. Eleven cues fetched from the browser would be
 * twenty-two round trips, each of which can fail on its own and half of which
 * would land a file the next one then has no room for. The server takes the whole
 * list, dedupes it, checks the audio cap ONCE against what the project already
 * holds, and answers with what it managed — which is the only place that
 * arithmetic can be done correctly, because the cap is the server's.
 *
 * @returns `{ sounds: [{key, query, kind, max_seconds, min_seconds}] }`, or null
 *          when neither pass is due and there is nothing to ask for.
 */
export function soundtrackRequest({ sounds, music } = {}) {
  const list = [];
  for (const sound of sounds || []) {
    list.push({ key: sound.key, query: sound.query, kind: "sfx", max_seconds: SFX_MAX_SECONDS });
  }
  if (music) {
    list.push({
      key: music.key,
      query: music.query,
      kind: "music",
      min_seconds: MUSIC_MIN_SECONDS,
    });
  }
  return list.length ? { sounds: list } : null;
}

/** The imported files, by cue key. `[]` and `{}` both read as "nothing came back". */
function importedBy(imported) {
  const map = new Map();
  for (const row of imported || []) {
    const key = cueKey(row && row.key ? row.key : row && row.query);
    if (key && row && row.upload_id) map.set(key, row);
  }
  return map;
}

/**
 * WHERE EVERY SOUND EFFECT GOES ON THE TIMELINE.
 *
 * ⚠ AT THE SHOT'S FIRST FRAME, NOT SOMEWHERE INSIDE IT. A cue is written for a
 * shot, and the one moment in a shot this pass can know about is the moment it
 * begins — anything else would be a guess about a picture the model was never
 * shown (the system prompt says so out loud: "you cannot see the pictures").
 *
 * ⚠ AND A CLIP RUNNING PAST ITS SHOT IS LEFT ALONE. `trackPlayMs` already stops
 * every clip at the end of the FILM, and a slam that rings over the next cut is
 * how sound actually works — trimming each one to its shot would give the film a
 * gate on every edit.
 *
 * @param cues     from `sfxCues`
 * @param imported what the server filed in — `[{key, upload_id, filename, duration_ms, …}]`
 * @returns {{ clips, missing }} — `clips` are `placeAudioUpload`-shaped, `missing`
 *          is `[{shot, query, why}]` for a cue nothing was found for
 */
export function sfxPlacements({ cues, imported } = {}) {
  const found = importedBy(imported);
  const clips = [];
  const missing = [];
  for (const cue of cues || []) {
    const file = found.get(cue.key);
    if (!file) {
      missing.push({ shot: cue.shot, query: cue.query, why: "no usable sound was found for it" });
      continue;
    }
    clips.push({
      upload_id: file.upload_id,
      filename: file.filename || "",
      duration_ms: Math.max(0, Math.round(Number(file.duration_ms) || 0)),
      attribution: file.attribution || "",
      // ⚠ WHAT WAS ACTUALLY SEARCHED FOR, when it was not what was asked for.
      // Carried for the report line, never for the mix. See `relaxed_to` on
      // `SoundtrackItem`: a sound found by widening the query is an answer to a
      // different question, and the user has to be able to see which.
      relaxedTo: file.relaxed_to || "",
      start_ms: cue.at_ms,
      offset_ms: 0,
      trim_ms: 0,
      volume: SFX_VOLUME,
      fade_in_ms: SFX_FADE_MS,
      fade_out_ms: SFX_FADE_MS,
      // Carried for the log line the panel prints, never for the mix.
      shot: cue.shot,
      query: cue.query,
    });
  }
  return { clips, missing };
}

/**
 * WHERE THE MUSIC GOES — one clip, or as many as it takes to reach the end.
 *
 * ⚠ THE LAST CLIP IS TRIMMED, WHICH IS WHAT PUTS THE FADE-OUT ON THE FINAL FRAME.
 * `trackPlayMs` would clamp an over-running clip to the film's length anyway, so
 * the mix would be right either way — but a `fade_out_ms` is measured from the
 * clip's own END, so an untrimmed last loop would fade out somewhere past the
 * final frame, i.e. never. Trimming it is the difference between a film that ends
 * and a film that stops.
 *
 * ⚠ AND THE LEVEL DEPENDS ON WHETHER ANYONE SPEAKS. See `MUSIC_VOLUME_*`.
 *
 * @param cue         from `musicCue`
 * @param imported    what the server filed in
 * @param totalMs     the film's length AFTER every other pass and every step
 * @param underSpeech is there a voiceover on this film
 * @returns {{ clips, why }} — `why` is set only when there are no clips
 */
export function musicPlacement({ cue, imported, totalMs, underSpeech } = {}) {
  if (!cue) return { clips: [], why: "no music was cued" };
  const film = Math.max(0, Math.round(Number(totalMs) || 0));
  if (!film) return { clips: [], why: "the film has no length to score" };
  const file = importedBy(imported).get(cue.key);
  if (!file) return { clips: [], why: `no usable music was found for “${cue.query}”` };

  const length = Math.max(0, Math.round(Number(file.duration_ms) || 0));
  const volume = underSpeech ? MUSIC_VOLUME_UNDER_SPEECH : MUSIC_VOLUME_ALONE;
  const base = {
    upload_id: file.upload_id,
    filename: file.filename || "",
    duration_ms: length,
    attribution: file.attribution || "",
    relaxedTo: file.relaxed_to || "",
    offset_ms: 0,
    volume,
  };

  // ⚠ A FILE WHOSE LENGTH WE DO NOT KNOW IS LAID ONCE AND NOT LOOPED. Freesound
  // sends its own duration and the browser re-measures the blob afterwards, so 0
  // here means something went wrong upstream — and looping on a length of 0 is an
  // infinite list. One clip is the safe read: the bed plays for however long the
  // file happens to run and the film simply gets quieter at the end.
  if (length <= 0) {
    return {
      clips: [
        {
          ...base,
          start_ms: 0,
          trim_ms: 0,
          fade_in_ms: MUSIC_FADE_IN_MS,
          fade_out_ms: MUSIC_FADE_OUT_MS,
        },
      ],
      why: "",
    };
  }

  const clips = [];
  // ⚠ A CEILING ON THE LOOP COUNT, because a 4-second sting under a 20-minute
  // film is 300 clips and a timeline row nobody can read. Past this the bed plays
  // as far as it reaches and stops, which is visible and fixable; 300 clips is
  // neither.
  // ⚠ AND IT IS ALSO WHAT KEEPS THE RUN UNDER `MAX_ANIMATIC_AUDIO_CLIPS`. 32
  // loops plus `MAX_SFX_CLIPS` sound effects plus a razored voiceover is inside
  // the server's ceiling with room to spare; without a bound here a short sting
  // under a long film would fail the SAVE rather than merely look silly.
  const MAX_LOOPS = 32;
  for (let at = 0, loop = 0; at < film && loop < MAX_LOOPS; at += length, loop += 1) {
    const room = film - at;
    const last = room <= length;
    clips.push({
      ...base,
      start_ms: at,
      // Only the final clip is cut, and only when it genuinely overruns.
      trim_ms: last && room < length ? room : 0,
      fade_in_ms: at === 0 ? MUSIC_FADE_IN_MS : 0,
      fade_out_ms: last ? Math.min(MUSIC_FADE_OUT_MS, room) : 0,
    });
  }
  return { clips, why: "" };
}

/**
 * ONE SENTENCE FOR THE WHOLE PASS, written from what actually landed.
 *
 * ⚠ IT NAMES WHAT IS MISSING AS WELL AS WHAT IS THERE. A pass that quietly
 * placed six of eleven cues and reported "sound added" is a pass the user trusts
 * once. `skipped` and `missing` are different failures — one was never asked for,
 * the other was asked for and not found — and both belong in the count.
 */
export function scoreReport({ sfx, music, sfxMissing, musicWhy } = {}) {
  const parts = [];
  const clips = (sfx || []).length;
  if (clips) {
    const sounds = new Set((sfx || []).map((c) => c.upload_id)).size;
    parts.push(
      `${clips} sound effect${clips === 1 ? "" : "s"} from ${sounds} ` +
        `recording${sounds === 1 ? "" : "s"}`
    );
  }
  const beds = (music || []).length;
  if (beds) {
    parts.push(
      beds === 1
        ? "a music bed under the whole film"
        : `a music bed looped ${beds} times to cover the film`
    );
  }
  // ⚠ A CUE THAT ONLY MATCHED ON THE WIDER SEARCH IS COUNTED SEPARATELY, because
  // it is a different kind of outcome from both "found" and "not found": the sound
  // on the timeline answers a narrower question than the plan asked. Naming the
  // count here and the cues themselves in the panel is what stops "2 sound effects
  // added" from quietly meaning "2 sounds of something".
  const widened = [...(sfx || []), ...(music || [])].filter((c) => c && c.relaxedTo);
  const lost = (sfxMissing || []).length;
  if (!parts.length) {
    return musicWhy && !clips && !lost
      ? `No sound was added — ${musicWhy}.`
      : "No sound was added.";
  }
  const tail = lost
    ? ` ${lost} cue${lost === 1 ? "" : "s"} found nothing usable and ${lost === 1 ? "was" : "were"} left out.`
    : "";
  const wide = widened.length
    ? ` ${widened.length} ${widened.length === 1 ? "was" : "were"} found on a wider search than the cue asked for.`
    : "";
  return `Added ${parts.join(" and ")}.${tail}${wide}`;
}
