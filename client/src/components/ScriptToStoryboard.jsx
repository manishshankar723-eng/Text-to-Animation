// Script → Storyboard flow:
//   Step "library"— "Your Storyboards": every board this user has generated,
//                   plus the New Storyboard card. This is where the flow opens,
//                   so returning users land on their saved work.
//   Step "form"   — single-page input (script → style → aspect), matches the
//                   Text-to-Image workflow's design language.
//   Step "review" — Stage C: the AI shot list, editable before generating panels
//                   (edit description, reorder, delete, add). Panel generation is
//                   the next build step (button shows a "coming soon" notice).
import { Fragment, useState, useRef, useEffect } from "react";
import * as api from "../api.js";
import StoryboardBoard from "./StoryboardBoard.jsx";
import StoryboardCast from "./StoryboardCast.jsx";
import StoryboardAssets from "./StoryboardAssets.jsx";
import StoryboardLibrary from "./StoryboardLibrary.jsx";
import BreakdownProgress, { SCRIPT_STEPS } from "./BreakdownProgress.jsx";
import PreflightModal from "./PreflightModal.jsx";
import ScriptLineBox from "./ScriptLineBox.jsx";
import GrowTextarea from "./GrowTextarea.jsx";
import DialogueEditor from "./DialogueEditor.jsx";
import WorldSetting from "./WorldSetting.jsx";
import ScriptPanel from "./ScriptPanel.jsx";
// ⚠ THE SCRIPT CHAT IS NOT IMPORTED HERE ANY MORE — see the note above the
// script box below. `ScriptChat.jsx` and `POST /script-chat` are deliberately
// left in the tree; the chat belongs AFTER a board exists, as the thing that
// edits it, not on the screen where the user is still handing over material.
import WorkflowIcon from "./WorkflowIcon.jsx";
// Style / aspect / genre lists live in one module so the Profile page's
// "usual choices" and this form can never offer different options.
//
// "rough-sketch" is the DEFAULT and is deliberately first: a plain grey
// storyboard thumbnail is what a board is FOR (staging and timing), it reads
// instantly, and because there's no rendered detail there's nothing for the
// model to get wrong — so it needs far fewer expensive re-draws. Anyone who
// wants a polished look just picks another style and gets the full flow.
import {
  STYLES,
  MORE_STYLES,
  ALL_STYLES,
  DEFAULT_STYLE,
  REFERENCE_FREE_STYLES,
  ASPECTS,
  DEFAULT_ASPECT,
  GENRES,
  MORE_GENRES,
  ALL_GENRES,
  MARKET_LANGUAGES,
} from "../storyboardOptions.js";

// A shot's position WITHIN its scene, derived from the current list rather than
// read off the stored `shot_number`. Moving, inserting or deleting a shot never
// renumbers the stored field, so a stored value goes stale the moment the user
// reorders — this can't.
function sceneShotNo(list, index) {
  const scene = list[index]?.scene_number;
  let n = 0;
  for (let i = 0; i <= index; i++) if (list[i]?.scene_number === scene) n++;
  return n;
}


// How many opening words of the script to use when the user types no title.
// Four keeps two boards from the same script distinguishable while still
// producing a filename you can read at a glance.
const TITLE_WORDS = 4;

// Text-readable script files we can parse in the browser. PDF/DOCX need
// server-side extraction (not built yet) — user pastes those for now.
const TEXT_EXTENSIONS = ["txt", "fountain", "fdx", "md", "text"];

/** Seconds as a runtime: "38s" under a minute, "1m 22s" over it.
 *
 * Exported so the board prints the same string. A film that reads "82s" in one
 * place and "1m 22s" in another reads as two different numbers. */
export function formatRuntime(seconds) {
  const n = Math.max(0, Math.round(Number(seconds) || 0));
  if (n < 60) return `${n}s`;
  const m = Math.floor(n / 60);
  const rest = n % 60;
  return rest ? `${m}m ${rest}s` : `${m}m`;
}

// ⚠ MODULE SCOPE, AND THAT IS THE WHOLE TRICK. This is initialised once per
// PAGE LOAD and is untouched by the component mounting again, which is exactly
// the distinction the restore below needs and the one a `useRef` cannot make:
//
//   fresh page load   → module re-evaluated → false → the card reopens
//   workflow switched away and back → same page → true → it does NOT
//   StrictMode's second mount → same page → true → it does NOT
//
// ⚠ It is NOT the "first mount wins" flag that failed for the storyboard draft.
// That one lived in a ref, StrictMode spent it on a mount nobody saw, and the
// built app then behaved differently from `npm run dev`. A module binding is
// not per-instance, so a double mount cannot spend it twice.
let conceptReopened = false;

export default function ScriptToStoryboard({
  onOpenAnimatic,
}) {
  // Open on the library so a returning user sees their saved storyboards first.
  const [step, setStep] = useState("library");

  // Form state
  //
  // ⚠ THERE IS ONE WAY IN NOW, NOT THREE. This form used to carry two tabs
  // (paste / upload) with a chat living inside the first of them — three doors
  // to the same `script` state, and a Generate button on two of them. `script`
  // is still the single value everything downstream reads; only the number of
  // controls writing into it has come down to one box plus an upload.
  const [script, setScript] = useState("");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  const [genre, setGenre] = useState("default"); // "default" = no bias
  const [customGenre, setCustomGenre] = useState("");
  // WHO THIS FILM IS FOR — ONE control, the language, and never a country.
  //
  // ⚠ THE COUNTRY PICKER WAS HERE AND WAS DELIBERATELY TAKEN OUT. Asked on the
  // way to a storyboard, "which market?" reads as a question about PRICES, and
  // somebody drawing two friends on a train has no answer and no reason to
  // want one. The country is worked out instead — from the language (see
  // `LANGUAGE_COUNTRY` in market.py), from the account default, or from the
  // script itself — so the money still comes out right without the question.
  // It survives on the profile, where it is a setting chosen once.
  //
  // ⚠ "" IS STILL A REAL ANSWER, NOT AN UNSET FIELD: nothing known anywhere
  // means the film shows no prices and no readable on-screen text at all,
  // which is the correct output when nobody has said who is watching.
  // Guessing is how an Indian creator's app promo came back priced in dollars.
  const [language, setLanguage] = useState("");
  // THE BRAND THIS FILM SELLS, if it sells one.
  //
  // ⚠ THE LOGO IS UPLOADED AND NEVER GENERATED, and that is not a preference —
  // an image model redraws a mark from its description every time and never
  // twice the same. One reported 28-panel promo came back with four unrelated
  // "Lickyeat" logos in it. With a file here, the model draws a flat
  // placeholder and the server pastes this exact PNG into every panel; with
  // none, it is told to invent nothing and leave app icons blank.
  const [brandName, setBrandName] = useState("");
  const [brandLogoId, setBrandLogoId] = useState("");
  const [brandLogoPreview, setBrandLogoPreview] = useState(null);
  const [brandBusy, setBrandBusy] = useState(false);
  const [brandError, setBrandError] = useState("");
  const brandFileRef = useRef(null);
  const [style, setStyle] = useState(DEFAULT_STYLE);
  const [customStyle, setCustomStyle] = useState("");
  const [aspect, setAspect] = useState(DEFAULT_ASPECT);
  const [customAspect, setCustomAspect] = useState("");
  // "＋ More" popups for the overflow genres / styles.
  const [genreMoreOpen, setGenreMoreOpen] = useState(false);
  const [styleMoreOpen, setStyleMoreOpen] = useState(false);
  const fileInputRef = useRef(null);
  const scriptRef = useRef(null);

  // Review state
  const [shots, setShots] = useState([]);
  const [characters, setCharacters] = useState([]);
  const [assets, setAssets] = useState([]);
  // The story's region / period / culture, read off the script by the breakdown
  // and editable by the user. Goes into EVERY image prompt (cast, props,
  // backgrounds, panels) so a non-Western story isn't drawn Western by default.
  const [world, setWorld] = useState({});
  // The resolved script text (pasted, or read out of an uploaded file). Shown in
  // full on the review step and saved with the board, so the line numbers on the
  // shot cards can be looked up — including on a duplicated board.
  const [scriptText, setScriptText] = useState("");
  // ⚠ THE RUNTIME THE USER APPROVED, carried from the concept card to the
  // breakdown and then shown beside the real one on the review step. Null
  // for a pasted script (nobody agreed a length) and for a resumed draft
  // (the target is not stored on the draft), and the chip simply hides.
  const [targetSeconds, setTargetSeconds] = useState(null);
  // Set true the moment the breakdown API call returns, so the progress ring
  // can race to 100% and THEN hand off to Review — instead of the old behaviour
  // where the call finishing froze the ring wherever it happened to be.
  const [breakdownDone, setBreakdownDone] = useState(false);
  const pendingBreakdown = useRef(null);
  // Character refs chosen on the cast step, carried into the assets step so both
  // sets of references reach panel generation together.
  const [characterRefs, setCharacterRefs] = useState({});

  // The launch the user has ASKED for but not yet confirmed: {charRefs,
  // assetRefs}. Non-null = the pre-flight modal is up. Nothing is generated
  // until it is confirmed, so reaching the board can never start images by
  // itself.
  const [preflight, setPreflight] = useState(null);

  // Everything the user sets up on the cast / props steps — the generated or
  // uploaded reference AND the edited description — lives HERE rather than in
  // those step components, because they unmount whenever the user steps away.
  // Keyed by lowercased name, so a step can re-seed itself when it mounts again
  // and the work survives Back → forward for the whole storyboard session.
  const [savedCastRefs, setSavedCastRefs] = useState({});
  const [savedAssetRefs, setSavedAssetRefs] = useState({});
  // Blob preview URLs are owned here too: the step components used to revoke
  // theirs on unmount, which is exactly what blanked the thumbnails on Back.
  const previewUrls = useRef([]);

  useEffect(
    () => () => previewUrls.current.forEach((u) => URL.revokeObjectURL(u)),
    []
  );

  // --- Profile defaults ----------------------------------------------------
  // The user's usual style / aspect / genre, set once on their profile. Applied
  // only to an UNTOUCHED form: once they've picked something on this form, or a
  // draft has been resumed into it, their choice wins over the default. An
  // empty profile field means "ask me each time" and changes nothing.
  const profileDefaultsApplied = useRef(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const p = await api.me();
        if (cancelled || profileDefaultsApplied.current) return;
        if (p?.default_style) setStyle((cur) => (cur === DEFAULT_STYLE ? p.default_style : cur));
        if (p?.default_aspect_ratio)
          setAspect((cur) => (cur === DEFAULT_ASPECT ? p.default_aspect_ratio : cur));
        if (p?.default_genre) setGenre((cur) => (cur === "default" ? p.default_genre : cur));
        // ⚠ THE LANGUAGE IS PREFILLED HERE TOO, and it is the one default that
        // changes what is DRAWN rather than how it looks: it decides the money
        // on a price tag and the language on a shop sign. Prefilled so a
        // creator who always makes films for one market never has to say so
        // twice — and still overridable on this form, board by board.
        //
        // ⚠ `default_country` is NOT prefilled, because there is no longer a
        // country control to prefill. It is not lost: the server reads it off
        // the account itself, as a layer under this form. See `_resolve_market`.
        if (p?.default_language) setLanguage((cur) => (cur === "" ? p.default_language : cur));
        profileDefaultsApplied.current = true;
      } catch {
        // No profile / offline — the built-in defaults are already in place.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // ⚠ THE APPROVAL GATE. A brief or an idea becomes a concept the user reads
  // and can edit, and NOTHING is drawn until they approve it. Non-null means
  // the concept step is what's on screen. `conceptSource` is what they pasted,
  // kept because a concept has no field for a product name or a required line
  // and the writer needs those.
  //
  // ⚠ DECLARED HERE, ABOVE THE AUTOSAVE, AND NOT DOWN WITH THE REST OF THE GATE
  // — the autosave below lists `concept` in its dependency array, and a
  // dependency array is evaluated DURING RENDER. `const` is not hoisted, so
  // leaving the declaration further down threw "Cannot access 'concept' before
  // initialization" and rendered the whole workflow as a WHITE PAGE. Moving
  // either one back is the same crash again.
  const [concept, setConcept] = useState(null);
  const [conceptSource, setConceptSource] = useState("");

  // --- Script autosave -----------------------------------------------------
  // A script only became durable once it had been turned into a board, so
  // anything typed and not yet generated died with a refresh. The draft is now
  // saved server-side (Mongo) on a debounce, and restored on mount.
  //
  // `draftReady` is the important bit: `script` starts as "", so without it the
  // debounce would fire on mount and OVERWRITE the saved draft with an empty
  // string before the GET ever came back. Nothing saves until the load settles.
  const [draftReady, setDraftReady] = useState(false);
  const [draftSavedAt, setDraftSavedAt] = useState(null);
  const draftLastSaved = useRef(null); // last text we actually persisted
  const draftLastConcept = useRef(null); // …and the last concept, as JSON

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const d = await api.getScriptDraft();
        if (cancelled) return;
        // Only seed an untouched box — never clobber something already typed
        // (the load is async; the user may have started writing meanwhile).
        if (d?.text) {
          setScript((cur) => (cur.trim() ? cur : d.text));
          if (d.title) setTitle((cur) => (cur.trim() ? cur : d.title));
        }
        // ⚠ THE CARD COMES BACK, AND ON A FRESH PAGE LOAD IT REOPENS.
        //
        // It was offered instead at first — a link in the form's status row —
        // and `tests/workflow_mount_check.py` proved that could never work: the
        // only route to the form from a cold start is "New storyboard", which
        // calls `resetWorkflow()` and clears the concept on the way. The offer
        // was unreachable by construction.
        //
        // ⚠ AND REOPENING IS NOT THE STORYBOARD-DRAFT BUG, because the latch is
        // per PAGE LOAD, not per mount — see `conceptReopened` above. Leaving
        // the workflow and coming back does not reopen anything. The two cases
        // differ in what is at stake as well: that one reopened a 29-shot board
        // somebody had already paid for; this is an unapproved card with a ←
        // out of it, and it is the screen the user was on when the page died.
        if (d?.concept) {
          setConcept((cur) => cur || d.concept);
          if (!conceptReopened) {
            conceptReopened = true;
            setStep((cur) => (cur === "library" ? "concept" : cur));
          }
        }
        draftLastSaved.current = d?.text || "";
        draftLastConcept.current = JSON.stringify(d?.concept || null);
        if (d?.updated_at) setDraftSavedAt(d.updated_at);
      } catch {
        // Autosave is a convenience — a failed load must never block the form.
      } finally {
        if (!cancelled) setDraftReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!draftReady) return;
    const conceptJson = JSON.stringify(concept || null);
    // Nothing changed in EITHER half. Editing one scene line has to save, so
    // the concept is compared too — and by value, because the card is rebuilt
    // as a new object on every keystroke.
    if (
      script === draftLastSaved.current &&
      conceptJson === draftLastConcept.current
    )
      return;
    const id = setTimeout(async () => {
      try {
        const saved = await api.saveScriptDraft({ text: script, title, concept });
        draftLastSaved.current = script;
        draftLastConcept.current = conceptJson;
        setDraftSavedAt(saved?.updated_at || new Date().toISOString());
      } catch {
        // Stay quiet: the text is still on screen, and the next keystroke
        // retries. Shouting about a failed autosave helps nobody mid-sentence.
      }
    }, 1200);
    return () => clearTimeout(id);
  }, [script, title, concept, draftReady]);

  // --- Storyboard draft (the REVIEW step's backing store) ------------------
  // A breakdown costs quota, and everything after it — edited shots, cast,
  // world, generated references — is hand-work. The server saves the breakdown
  // as a DRAFT job; this keeps that record in step with what's on screen.
  const [draftJobId, setDraftJobId] = useState(null);
  const [reviewSavedAt, setReviewSavedAt] = useState(null);
  // Same guard as the script draft, and it matters more here: `shots` is [] on
  // mount, so an unguarded autosave would PATCH an empty shot list over a
  // perfectly good draft before the resume request had even returned.
  const draftHydrated = useRef(false);
  const reviewLastSaved = useRef("");

  // ⚠ THE UNFINISHED BOARD IS OFFERED, NEVER TAKEN. This effect used to hydrate
  // itself and `setStep("review")` on every mount. That is right after a
  // refresh and wrong every other time: switching to Plan & Script and back
  // UNMOUNTS this component, so returning to the workflow re-ran it and dropped
  // the user straight into a review step they had deliberately walked out
  // of — showing a board from an EARLIER session, with nothing on screen saying
  // where it had come from. Reported exactly that way: *"mai abhi aage nhi
  // dawaya tha, mai back aaya tha aur Start over button bhi nhi dabaya."*
  //
  // ⚠ AND "ONLY AUTO-OPEN ON THE FIRST MOUNT" IS NOT THE FIX. React's
  // StrictMode mounts every component twice in development, so any
  // first-mount-wins flag is spent by a mount the user never saw, and the
  // behaviour would then differ between `npm run dev` and the built app — the
  // worst kind of bug to chase.
  //
  // So this stops guessing how the user got here. The workflow always opens on
  // its own front door — the library — and the unfinished board waits there as
  // the first ROW of "Recent Storyboards", beside the finished ones, carrying
  // its shot count and a Resume button. Nothing is lost, which was the whole
  // point of saving it, and nobody is moved somewhere they did not ask to go.
  //
  // ⚠ AND IT IS OFFERED EXACTLY ONCE. It briefly had a strip on the dashboard
  // AND a banner on this form AND the library row — three places for one
  // record. *"Script to Storyboard se bhi hata do, only recent mein hi rakho."*
  // The row is the one place; `StoryboardLibrary.renderDraftRow` owns it.

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        // ⚠ NOTHING IS READ OFF THIS ANY MORE — the draft is OFFERED IN ONE
        // PLACE, as a row in "Recent Storyboards" (see StoryboardLibrary's
        // `renderDraftRow`), and the form used to repeat the same offer as a
        // banner. What this call is still for is the `draftHydrated` flag
        // below: `shots` is [] on mount, so an unguarded autosave would PATCH
        // an empty shot list over a perfectly good draft before the server had
        // even answered. The await IS the guard.
        await api.getStoryboardDraft();
        if (cancelled) return;
      } catch {
        // No draft, or the server is unreachable — start clean.
      } finally {
        if (!cancelled) draftHydrated.current = true;
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  /** Take the offered draft: load all of it and reopen the review step.
   *
   *  `record` lets the LIBRARY step resume the strip it fetched itself; with
   *  nothing passed this resumes the banner on the form. Both are the same
   *  draft — two doors into one room. */
  function resumeDraft(record) {
    const d = record;
    if (!d?.job_id) return;
    // A resumed draft carries the settings it was reviewed with. Those beat the
    // profile defaults — block that effect whichever order they finish in, or
    // reopening a 9:16 draft could snap it back to your usual 16:9.
    profileDefaultsApplied.current = true;
    setDraftJobId(d.job_id);
    setShots(d.shots || []);
    setCharacters(d.characters || []);
    setAssets(d.assets || []);
    setWorld(d.world || {});
    setScriptText(d.script || "");
    // ⚠ THE BRIEF BOX KEEPS WHATEVER THE USER HAS TYPED. Resuming a board must
    // not overwrite a sentence they are part-way through writing.
    setScript((cur) => (cur.trim() ? cur : d.script || ""));
    if (d.title) setTitle((cur) => (cur.trim() ? cur : d.title));
    if (d.style) setStyle(d.style);
    if (d.aspect_ratio) setAspect(d.aspect_ratio);
    if (d.genre) setGenre(d.genre);
    if (d.character_refs) setCharacterRefs(d.character_refs);
    // ⚠ THE REFERENCES ALREADY PAID FOR COME BACK WITH THE SHOTS. Reported:
    // *"mai back aaya to mera ananya wala photo dikh hi nhi raha hai … baar
    // baar generate karna pare, usko paisa lagta hai."* Leaving the workflow
    // unmounts this component, so the blob previews die with it; only the ids
    // on the draft survive, and until now they were never handed back to the
    // cast and props steps at all.
    restoreSavedRefs(setSavedCastRefs, d.character_refs, d.character_takes);
    restoreSavedRefs(setSavedAssetRefs, d.asset_refs, d.asset_takes);
    if (d.updated_at) setReviewSavedAt(d.updated_at);
    setStep("review");
  }

  // The effect that gives a draft-less session a draft of its own lives further
  // down, beside the board state — it has to read `jobId`, which is declared
  // there, and a dependency array is evaluated during RENDER, so referencing it
  // from up here is a ReferenceError on every render, not a subtle bug.


  useEffect(() => {
    if (!draftHydrated.current || !draftJobId) return;
    if (!shots.length) return; // never save an empty shot list over real work
    const payload = {
      shots,
      characters,
      assets,
      world,
      title: effectiveTitle(),
      style: effectiveStyle(),
      aspect_ratio: effectiveAspect(),
      genre: effectiveGenre(),
      // ⚠ THE CAST AND PROPS STEPS ARE THE LIVE TRUTH, and they save as soon
      // as a reference lands — not when the user walks forward off the step.
      // `characterRefs` is only filled on the way OUT of the cast step, so a
      // draft saved before that would have forgotten every image just drawn.
      // ⚠ ONE KEY SHAPE. `savedCastRefs` is keyed by `refKey` (lower-cased) and
      // `characterRefs` by the display name, so merging them raw would file
      // ANANYA twice. The server normalises names before matching, so the
      // lower-cased key is the one that always works — put both in it.
      character_refs: { ...refIdsOf(byRefKey(characterRefs)), ...refIdsOf(savedCastRefs) },
      asset_refs: refIdsOf(savedAssetRefs),
      character_takes: refTakesOf(savedCastRefs),
      asset_takes: refTakesOf(savedAssetRefs),
    };
    const sig = JSON.stringify(payload);
    if (sig === reviewLastSaved.current) return;
    const id = setTimeout(async () => {
      try {
        const saved = await api.saveStoryboardDraft(draftJobId, payload);
        reviewLastSaved.current = sig;
        setReviewSavedAt(saved?.updated_at || new Date().toISOString());
      } catch {
        // A promoted draft answers 409 — expected once Generate has run, and
        // not something to bother the user about mid-edit.
      }
    }, 1000);
    return () => clearTimeout(id);
  }, [
    draftJobId,
    shots,
    characters,
    assets,
    world,
    characterRefs,
    // References are part of the draft now, so drawing one has to trigger the
    // save that keeps it.
    savedCastRefs,
    savedAssetRefs,
    title,
    style,
    aspect,
    genre,
  ]);

  function refKey(name) {
    return (name || "").trim().toLowerCase();
  }

  // Called by the cast / props steps whenever a durable field changes.
  function saveRefFields(setter, name, fields) {
    const key = refKey(name);
    if (!key) return;
    if (fields.previewUrl) previewUrls.current.push(fields.previewUrl);
    setter((m) => ({ ...m, [key]: { ...m[key], ...fields } }));
  }

  // ⚠ WHAT GOES ON THE DRAFT, AND WHY IT IS NOT THE WHOLE THING. `previewUrl`
  // is an object URL for a blob this browser holds; it dies with the page and
  // means nothing to another machine. The reference_id does not — the image is
  // on the server under it, forever. So the draft stores IDS and the picture is
  // re-fetched, exactly as the library re-fetches board covers.
  // `{Name: id}` → `{name: {referenceId}}`, so a display-name map can go
  // through the same reader as the steps' own saved state.
  function byRefKey(map) {
    const out = {};
    for (const [name, referenceId] of Object.entries(map || {})) {
      const key = refKey(name);
      if (key && referenceId) out[key] = { referenceId };
    }
    return out;
  }
  function refIdsOf(saved) {
    const out = {};
    for (const [key, v] of Object.entries(saved || {})) {
      if (v?.referenceId) out[key] = v.referenceId;
    }
    return out;
  }
  function refTakesOf(saved) {
    const out = {};
    for (const [key, v] of Object.entries(saved || {})) {
      const takes = (v?.versions || [])
        .filter((t) => t?.referenceId)
        .map((t) => ({ reference_id: t.referenceId, uploaded: Boolean(t.uploaded) }));
      if (takes.length) out[key] = takes;
    }
    return out;
  }

  /** Put a draft's saved references back on the cast / props steps.
   *
   *  ⚠ THE PICTURE IS FETCHED, NOT REMEMBERED. Only the ACTIVE take is pulled
   *  down here: a draft can carry a dozen names with three takes each, and
   *  fetching every one of them on resume would drag megabytes the user may
   *  never look at. The other takes keep their ids and their image arrives the
   *  moment the ‹ › arrows land on them — see `pickVersion` in the steps.
   */
  function restoreSavedRefs(setter, ids, takes) {
    const seeded = {};
    for (const [rawKey, referenceId] of Object.entries(ids || {})) {
      // ⚠ NORMALISED, BECAUSE THE TWO SOURCES DISAGREE ON CASE. A draft's map
      // was written by `saveRefFields`, so its keys are already `refKey`'d; a
      // BOARD's map is whatever `StoryboardCast` sent up, which is the
      // character's own name — "ANANYA". The cast and props steps look
      // themselves up lower-cased, so an un-normalised key silently matches
      // nothing: four empty cards and a "(skip refs)" button, over references
      // that were paid for and are sitting on the server.
      const key = refKey(rawKey);
      if (!key) continue;
      const list = (takes || {})[rawKey] || (takes || {})[key]
        || [{ reference_id: referenceId }];
      const versions = list.map((t) => ({
        referenceId: t.reference_id,
        previewUrl: null,
        uploaded: Boolean(t.uploaded)
      }));
      let active = versions.findIndex((v) => v.referenceId === referenceId);
      if (active < 0) {
        // The live id isn't in the take list (an older draft, or a list that
        // was trimmed). Trust the live id and make it the only take, rather
        // than showing arrows that can't reach what is on screen.
        versions.push({ referenceId, previewUrl: null, uploaded: false });
        active = versions.length - 1;
      }
      seeded[key] = {
        referenceId,
        previewUrl: null,
        uploaded: Boolean(versions[active].uploaded),
        versions,
        activeVersion: active
      };
    }
    if (!Object.keys(seeded).length) return;
    setter(seeded);
    // Then fill in the live picture for each, as it arrives.
    for (const [key, entry] of Object.entries(seeded)) {
      api
        .fetchReferenceImage(entry.referenceId)
        .then((url) => {
          previewUrls.current.push(url);
          setter((m) => {
            const cur = m[key];
            if (!cur || cur.referenceId !== entry.referenceId) return m;
            const versions = (cur.versions || []).map((v) =>
              v.referenceId === entry.referenceId ? { ...v, previewUrl: url } : v
            );
            return { ...m, [key]: { ...cur, previewUrl: url, versions } };
          });
        })
        // A reference the server no longer has just leaves the placeholder —
        // the card still works and the name can be drawn again.
        .catch(() => {});
    }
  }

  function clearSavedRefs() {
    previewUrls.current.forEach((u) => URL.revokeObjectURL(u));
    previewUrls.current = [];
    setSavedCastRefs({});
    setSavedAssetRefs({});
  }

  // Board state
  const [jobId, setJobId] = useState(null);
  // Signature of the shots/style/aspect that produced the CURRENT board. Lets us
  // tell "nothing changed, reopen the existing board" from "shots edited,
  // regenerate" — so going Back to shots and returning doesn't throw away the
  // panels already drawn and start over.
  const [generatedSig, setGeneratedSig] = useState(null);
  // Where the board was opened from, so ← Back goes somewhere that still has
  // content: the review step for a board we just generated, the library for a
  // saved board re-opened from a card (whose shots aren't loaded).
  const [boardOrigin, setBoardOrigin] = useState("review");

  // ⚠ ARMED WHEN A SAVED BOARD IS RE-OPENED, STAMPED ONE RENDER LATER.
  //
  // `currentSig()` reads shots, style, aspect, world, market and brand out of
  // state, and `setState` does not land until the next render — so computing it
  // inside the open handler would sign the board with the PREVIOUS board's
  // values. React batches the whole handler into one render, so an effect keyed
  // on `jobId` runs once everything has actually arrived.
  //
  // ⚠ AND IT MATTERS FINANCIALLY. Without the signature the board reads as
  // out of date, "→ Back to your storyboard" disappears, and the only way back
  // to panels that already exist is to draw all fifteen again.
  //
  // ⚠ STATE, NOT A REF, AND THAT IS THE WHOLE FIX. A ref does not re-render,
  // so arming it AFTER `setJobId` meant the effect had already run and looked
  // at a null ref, and `jobId` never changed again to run it a second time —
  // the stamp silently never happened, and "→ Back to your storyboard"
  // silently never appeared. Caught in a browser; nothing else could see it.
  const [sigStampJob, setSigStampJob] = useState(null);

  // ⚠ A SESSION WITH SHOTS BUT NO DRAFT SAVES NOTHING, SILENTLY. The autosave
  // below is keyed on `draftJobId`, and only a BREAKDOWN mints one — it has
  // just spent money, so it writes the result down before showing it. Duplicate
  // deliberately skips the breakdown (that is its whole point: reuse the shots,
  // don't pay again), so it landed on the review step with no draft, and from
  // there every edit and every reference image the user PAID FOR on the cast
  // and props steps lived only in this component. Walking out to Home unmounted
  // it and took the lot.
  //
  // Reported exactly that way: a character reference was drawn, the user left,
  // came back, pressed Resume — and an unrelated project opened, because theirs
  // had never been written down and Resume could only offer what the server
  // actually had.
  //
  // So: if we are reviewing real shots and nothing is backing them, make a
  // record. `POST /storyboards/draft` calls no model and spends no quota.
  //
  // ⚠ `jobId` IS PART OF THE GUARD. Stepping Back from a finished board also
  // lands on the review step with shots and no draft — but that work is already
  // saved, as a board, and a draft beside it would be a second record of one
  // storyboard. ⚠ And `creatingDraft` is a ref, not state: StrictMode mounts
  // twice in development, and two runs here would mean two drafts.
  const creatingDraft = useRef(false);
  useEffect(() => {
    if (!draftHydrated.current) return;
    if (step !== "review" || draftJobId || jobId) return;
    if (!shots.length || creatingDraft.current) return;
    creatingDraft.current = true;
    (async () => {
      try {
        const d = await api.createStoryboardDraft({
          shots,
          title: effectiveTitle(),
          script: scriptText || "",
          style: effectiveStyle(),
          aspect_ratio: effectiveAspect(),
          genre: effectiveGenre(),
          characters,
          assets,
          world,
        });
        if (d?.job_id) {
          setDraftJobId(d.job_id);
          setReviewSavedAt(d.updated_at || new Date().toISOString());
        }
      } catch {
        // Storage is down, or the plan does not carry this workflow. The
        // session still works exactly as it did before — it just isn't saved,
        // which is the old behaviour and not worth a dialog mid-review.
      } finally {
        creatingDraft.current = false;
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, draftJobId, jobId, shots.length]);

  const [busy, setBusy] = useState(false);
  // ⚠ SEPARATE FROM `busy` ON PURPOSE. `busy` swaps the whole form out for the
  // breakdown's progress ring; reading the box is a second or two in front of
  // that and must not throw the form off screen — the user may be about to be
  // asked a question and need to see what they typed.
  const [reading, setReading] = useState(false);
  // What the intake said, when it said anything other than "this is a script":
  // {kind, reason, question}. Non-null = the panel under the box is showing.
  const [intake, setIntake] = useState(null);
  // ⚠ `concept` and `conceptSource` are declared ABOVE, with the autosave that
  // persists them — see the note there. Everything else about the gate lives
  // here.
  const [developing, setDeveloping] = useState(false);
  // Writing the approved concept out as a real script — the long call.
  const [writing, setWriting] = useState(false);
  const [scriptWritten, setScriptWritten] = useState(false);
  const pendingScript = useRef(null);
  // "brief" or "idea" — only used for the sentence on the concept screen.
  const conceptKind = useRef("idea");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const hasScript = script.trim().length > 0 || Boolean(file);
  // Style + aspect are pre-selected, so only the script gates generation.
  const canGenerate = hasScript && Boolean(style) && Boolean(aspect);

  const styleLabel =
    style === "custom" && customStyle.trim()
      ? customStyle.trim()
      : ALL_STYLES.find((s) => s.id === style)?.label || style;

  // The style value sent to the backend: for "Add Your Own Style" send the typed
  // text (used directly as the panel art direction); otherwise the style id.
  function effectiveStyle() {
    if (style === "custom") return customStyle.trim() || "custom";
    return style;
  }

  // The audience, as the server wants it. ⚠ ALWAYS AN OBJECT, NEVER NULL, even
  // when it is blank: the server has to be able to tell "the form said nothing"
  // (this, empty) from "the form was never asked" (an older client sending no
  // field at all), because only the first one is allowed to stop the account
  // default being applied.
  //
  // ⚠ NO `country` KEY, because this form no longer has a country control and
  // sending a permanently-blank one would be a lie about what was asked. The
  // country arrives from the layers underneath instead — the account default,
  // the script's own guess, and failing both the language above.
  function effectiveMarket() {
    return { language: language.trim() };
  }

  // The brand, as the server wants it. Always an object — an empty one is a
  // real answer meaning "this film sells nothing, invent no logo".
  function effectiveBrand() {
    return { name: brandName.trim(), logo_ref_id: brandLogoId };
  }

  async function uploadLogo(file) {
    if (!file || brandBusy) return;
    setBrandBusy(true);
    setBrandError("");
    try {
      const res = await api.uploadBrandLogo(file);
      setBrandLogoId(res.reference_id);
      // ⚠ PREVIEWED FROM THE LOCAL FILE, not by fetching the saved one back.
      // The preview route needs the auth header, so an <img src> pointed at it
      // renders a broken icon — the same reason the cast page previews from a
      // blob. Revoked on replace so a long session doesn't leak object URLs.
      setBrandLogoPreview((old) => {
        if (old) URL.revokeObjectURL(old);
        return URL.createObjectURL(file);
      });
    } catch (e) {
      setBrandError(e.message);
    } finally {
      setBrandBusy(false);
    }
  }

  function clearLogo() {
    setBrandLogoId("");
    setBrandError("");
    setBrandLogoPreview((old) => {
      if (old) URL.revokeObjectURL(old);
      return null;
    });
    if (brandFileRef.current) brandFileRef.current.value = "";
  }

  // Does the chosen style skip the cast + props steps? (Rough Sketch does — see
  // REFERENCE_FREE_STYLES.) Read from the EFFECTIVE style, so switching style
  // at any point — including inside the pre-flight modal — changes the flow.
  function skipsRefs() {
    return REFERENCE_FREE_STYLES.has(effectiveStyle());
  }

  // The aspect ratio sent to the backend: the typed W:H for Custom, else the id.
  function effectiveAspect() {
    if (aspect === "custom") return customAspect.trim() || DEFAULT_ASPECT;
    return aspect;
  }

  function pickFile(f) {
    if (!f) return;
    setFile(f);
    setError("");
  }

  // The name this storyboard is saved under. Falls back to the script's first
  // non-empty line so a saved board is never just called "Storyboard".
  function effectiveTitle() {
    const typed = title.trim();
    if (typed) return typed;
    // No title typed → the script's OPENING WORDS, not its whole first line.
    // This name becomes the board's card AND the downloaded PDF/ZIP filename,
    // so a full sentence made for an unusable file name.
    const firstLine = script
      .split("\n")
      .map((l) => l.trim())
      .find((l) => l.length > 0);
    if (firstLine) {
      const words = firstLine.split(/\s+/).slice(0, TITLE_WORDS).join(" ");
      return words.slice(0, 40).replace(/[.,;:!?—–-]+$/, "").trim() || firstLine.slice(0, 40);
    }
    if (file?.name) return file.name.replace(/\.[^.]+$/, "");
    return "Untitled storyboard";
  }

  // Re-select the chips for a saved board's settings. Anything that isn't one
  // of our known ids came from a "custom" field, so restore it as custom text.
  function applySavedSettings({ style: s, aspect_ratio: a, genre: g }) {
    if (s) {
      const known = ALL_STYLES.some((x) => x.id === s && x.id !== "custom");
      setStyle(known ? s : "custom");
      setCustomStyle(known ? "" : s);
    }
    if (a) {
      const known = ASPECTS.some((x) => x.id === a);
      setAspect(known ? a : "custom");
      setCustomAspect(known ? "" : a);
    }
    // Genres are stored as their readable label, so match on that too.
    if (g) {
      const match = ALL_GENRES.find(
        (x) => x.id === g || x.label.replace(/^\S+\s+/, "") === g
      );
      setGenre(match ? match.id : "custom");
      setCustomGenre(match ? "" : g);
    }
  }

  // The genre string sent to the breakdown: "" for Default, the typed text for
  // Custom, otherwise the readable genre name (label minus its emoji).
  function effectiveGenre() {
    if (genre === "custom") return customGenre.trim();
    if (!genre || genre === "default") return "";
    const g = ALL_GENRES.find((x) => x.id === genre);
    return g ? g.label.replace(/^\S+\s+/, "") : genre;
  }

  function handleDrop(e) {
    e.preventDefault();
    setDragOver(false);
    pickFile(e.dataTransfer.files?.[0]);
  }

  // Resolve the script text from the paste box or an uploaded text file.
  async function resolveScriptText() {
    const pasted = script.trim();
    if (pasted) return pasted;
    if (file) {
      const ext = (file.name.split(".").pop() || "").toLowerCase();
      if (TEXT_EXTENSIONS.includes(ext)) {
        return (await file.text()).trim();
      }
      throw new Error(
        "PDF/DOCX text extraction is coming soon — please paste your script text for now."
      );
    }
    return "";
  }

  /** Create storyboard → read what was given FIRST, then decide what to do.
   *
   * ⚠ THIS IS THE WHOLE POINT OF THE INTAKE. Before it, every box of text went
   * into the breakdown as a script, so one line of premise came back as a
   * twenty-panel film with a cast, locations and dialogue nobody wrote — drawn
   * and charged for without anyone being asked. A script still goes straight
   * through, untouched and usually without a model call; anything else stops
   * here and says so.
   */
  async function handleGenerate() {
    if (!canGenerate || busy || reading || developing) return;
    setError("");
    setNotice("");
    setIntake(null);

    let text;
    try {
      text = await resolveScriptText();
    } catch (e) {
      setError(e.message);
      return;
    }
    if (!text.trim()) {
      setIntake({ kind: "empty", reason: "", question: "" });
      return;
    }

    setReading(true);
    let verdict;
    try {
      verdict = await api.intakeScript(text);
    } catch {
      // ⚠ FAIL OPEN, ALWAYS. The intake is a helper, not a gate: if it is down
      // the user still gets the storyboard they asked for, exactly as they did
      // before this step existed. Blocking a board on a classifier would be a
      // worse bug than the one the classifier fixes.
      verdict = { kind: "script" };
    }
    setReading(false);

    const kind = verdict.kind || "idea";

    // ⚠ A BRIEF OR AN IDEA GOES TO THE APPROVAL GATE, NOT TO THE BREAKDOWN.
    // This is the whole point of the redesign: the invention happens where the
    // user can see it and change it, before a single image is paid for.
    if (kind === "brief" || kind === "idea") {
      setDeveloping(true);
      try {
        const res = await api.developConcept(text, {
          kind,
          genre: effectiveGenre(),
          style: effectiveStyle(),
          aspectRatio: effectiveAspect(),
        });
        setConcept(res.concept);
        setConceptSource(text);
        conceptKind.current = kind;
        setStep("concept");
      } catch (e) {
        // ⚠ NO FALLING THROUGH TO THE BREAKDOWN HERE, unlike the intake above.
        // The intake failing means "we could not tell"; this failing means we
        // could not work out what the film is — and building one anyway is the
        // silent invention the gate exists to stop. Say so and stay put.
        setError(e.message);
      } finally {
        setDeveloping(false);
      }
      return;
    }

    if (kind !== "script") {
      setIntake({
        kind,
        reason: verdict.reason || "",
        question: verdict.question || "",
        // ⚠ THE RESOLVED TEXT, NOT `script`. It may have come out of an
        // uploaded file, in which case the box is empty.
        text,
      });
      return;
    }
    startBreakdown(text);
  }

  // ---- The approval gate --------------------------------------------------

  /** "a brief" / "an idea" — how the concept screen refers back to the input.
   *  Kept from the intake so the wording matches what actually happened. */
  function intakeKindWord() {
    return conceptKind.current === "brief" ? "a brief" : "an idea";
  }

  /** Enough left on the card to write a film from. ⚠ The user can delete every
   *  scene and empty the premise; approving that would send the writer a blank
   *  page and get back an invention nobody approved. */
  function conceptReady() {
    if (!concept) return false;
    const hasScenes = (concept.key_scenes || []).some((s) => (s || "").trim());
    return Boolean((concept.premise || "").trim()) || hasScenes;
  }

  /** Change one field of the concept on screen. Every field is editable. */
  function updateConcept(patch) {
    setConcept((c) => ({ ...(c || {}), ...patch }));
  }

  function updateKeyScene(i, value) {
    setConcept((c) => ({
      ...c,
      key_scenes: (c.key_scenes || []).map((s, idx) => (idx === i ? value : s)),
    }));
  }

  /** Move one key scene up or down the list.
   *
   * ⚠ ORDER IS THE FILM HERE — these lines become the panels in exactly this
   * sequence — and "＋ Add a scene" can only APPEND. Reported mid-test: a shot
   * of the idol on its own was added to fill a real gap, landed at position 7,
   * and belonged at position 3 with no way to get there. The card is the one
   * screen where everything is meant to be editable, and its most important
   * field was the one thing that could not be rearranged.
   *
   * Mirrors `moveShot()` below, down to the silent no-op at either end — the
   * buttons are disabled there, and a keyboard or a double-click that beats the
   * re-render must not wrap the list around.
   */
  function moveKeyScene(i, dir) {
    setConcept((c) => {
      const scenes = [...((c || {}).key_scenes || [])];
      const j = i + dir;
      if (j < 0 || j >= scenes.length) return c;
      [scenes[i], scenes[j]] = [scenes[j], scenes[i]];
      return { ...c, key_scenes: scenes };
    });
  }

  /** ⚠ APPROVED → a real SCRIPT → the breakdown. Never concept → shots.
   *
   * The review step, ScriptPanel and every shot card's "FROM YOUR SCRIPT ·
   * LINE 12" need a script to point at, so the approved concept is written out
   * by plan_agent.write_script() — whose format is already a contract with
   * script_breakdown.py — and the board is built from THAT text.
   */
  async function approveConcept() {
    if (writing) return;
    setError("");
    setNotice("");
    setScriptWritten(false);
    pendingScript.current = null;
    setWriting(true);
    try {
      const res = await api.conceptToScript(concept, {
        source: conceptSource,
        language,
      });
      pendingScript.current = res;
      setScriptWritten(true); // lets the ring finish before we move on
    } catch (e) {
      setError(e.message);
      setWriting(false);
      setScriptWritten(false);
      pendingScript.current = null;
    }
  }

  /** Called by the ring once "Writing your script" has reached 100%. */
  function finishScript() {
    const res = pendingScript.current;
    pendingScript.current = null;
    if (!res) return;
    setScriptWritten(false);
    setWriting(false);
    // The approved concept's title names the board, unless the user typed one.
    if (res.title && !title.trim()) setTitle(res.title);
    // ⚠ THE BOX KEEPS THE USER'S OWN WORDS. The written script is what the
    // board is built from and what the review step shows; overwriting their
    // brief with it would throw away the thing they can edit and re-run.
    //
    // ⚠ AND THE LENGTH TRAVELS WITH IT. `res.seconds` is what the concept
    // card said and what the script was written to; the breakdown used to be
    // the one stage that never heard the number, and boarded a 30-second
    // film as 29 shots and 1m 04s.
    startBreakdown(res.script, res.seconds);
  }

  /** Break `text` into shots and move to the review step.
   *
   *  `seconds` is the approved runtime, when there is one. A script that was
   *  pasted rather than written from a concept has no agreed length, and
   *  passing nothing is the honest answer — the breakdown then boards it
   *  without a duration budget, exactly as it always did.
   */
  async function startBreakdown(text, seconds = null) {
    if (busy) return;
    setError("");
    setNotice("");
    setIntake(null);
    setBreakdownDone(false);
    pendingBreakdown.current = null;
    setTargetSeconds(seconds || null);
    setBusy(true);
    try {
      if (text.trim().length < 20) {
        throw new Error("Please provide at least a few sentences of script.");
      }
      setScriptText(text); // what the review step shows, line for line
      const res = await api.breakdownScript(text, {
        style: effectiveStyle(),
        aspectRatio: effectiveAspect(),
        genre: effectiveGenre(),
        // For the NAME only — so "[Your App Name]" never reaches a shot.
        brand: effectiveBrand(),
        title: effectiveTitle(),
        // The length the film is meant to be. See startBreakdown's note.
        seconds,
      });
      // Hold the result and let the ring finish to 100%. finishBreakdown()
      // (called by the ring on completion) applies it and moves to Review.
      pendingBreakdown.current = res;
      setBreakdownDone(true);
    } catch (e) {
      setError(e.message);
      setBusy(false);
      setBreakdownDone(false);
      pendingBreakdown.current = null;
    }
  }

  // Called by BreakdownProgress once the ring has reached 100%.
  function finishBreakdown() {
    const res = pendingBreakdown.current;
    pendingBreakdown.current = null;
    if (!res) return;
    setShots(res.shots || []);
    setCharacters(res.characters || []);
    setAssets(res.assets || []);
    setWorld(res.world || {});
    // The server saved this breakdown as a DRAFT job. Holding its id is what
    // lets every later edit be written back, so a refresh on the review step no
    // longer throws away work the breakdown had to be paid for.
    setDraftJobId(res.draft_job_id || null);
    draftHydrated.current = true;
    // A new breakdown is a new cast — drop refs saved for the previous script
    // so a same-named character can't inherit the old picture.
    clearSavedRefs();
    setBreakdownDone(false);
    setBusy(false);
    setStep("review");
  }

  // ---- Review handlers ----

  /** The film's length: every shot's own seconds, added up. 0 when none of them
   *  carry one (an older board, from before the breakdown returned it). */
  function totalSeconds() {
    return shots.reduce((sum, sh) => sum + (Number(sh.duration_seconds) || 0), 0);
  }

  /** Is the board meaningfully longer than the film the user approved?
   *  False whenever no length was agreed — a pasted script has no target to
   *  be over, and colouring the chip red would be inventing one. */
  function overRunning() {
    return Boolean(targetSeconds) && totalSeconds() > targetSeconds * 1.2;
  }

  function updateShot(i, patch) {
    setShots((s) => s.map((sh, idx) => (idx === i ? { ...sh, ...patch } : sh)));
  }
  function deleteShot(i) {
    setShots((s) => s.filter((_, idx) => idx !== i));
  }
  function moveShot(i, dir) {
    setShots((s) => {
      const j = i + dir;
      if (j < 0 || j >= s.length) return s;
      const copy = [...s];
      [copy[i], copy[j]] = [copy[j], copy[i]];
      return copy;
    });
  }
  function blankShot(sceneNumber) {
    return {
      scene_number: sceneNumber || 1,
      shot_number: 0,
      description: "",
      characters: [],
      // A hand-added shot has no script behind it, so nothing is spoken in it
      // until the user writes a line.
      dialogue: [],
      location: "",
      camera: "",
      // A shot the user added by hand came from no script line.
      script_line: "",
      script_line_start: null,
      script_line_end: null,
    };
  }
  function addShot() {
    setShots((s) => [...s, blankShot(s.at(-1)?.scene_number || 1)]);
  }
  // Insert a blank shot right AFTER position `index` (between two shots).
  function insertShot(index) {
    setShots((s) => {
      const copy = [...s];
      copy.splice(index + 1, 0, blankShot(s[index]?.scene_number || 1));
      return copy;
    });
  }
  // The ACTIVE cast = only characters that appear in the current (edited) shots,
  // deduped, enriched with descriptions from the breakdown when available. This
  // shrinks as the user deletes shots, so the cast count stays honest.
  function computeCast() {
    const descByName = new Map(
      characters.map((c) => [c.name.trim().toLowerCase(), c])
    );
    const byKey = new Map();
    for (const sh of shots) {
      // A shot naming the same person twice still only counts once — and the
      // key is lower-cased BEFORE the de-duplication, or "Ananya" and "ANANYA"
      // in one shot's list would count as two appearances of her.
      const inThisShot = new Map();
      for (const raw of sh.characters || []) {
        const name = (raw || "").trim();
        if (name) inThisShot.set(name.toLowerCase(), name);
      }
      for (const [key, name] of inThisShot) {
        const existing = byKey.get(key);
        if (existing) {
          existing.shotCount += 1;
          continue;
        }
        byKey.set(key, {
          ...(descByName.get(key) || { name, description: "" }),
          // ⚠ HOW MUCH FILM THIS FACE IS ACTUALLY IN. A reference costs an
          // image, and the cast list never said who was worth one: a board
          // came back with a full character sheet for an artisan who appears
          // ONLY as a pair of hands in a single close-up. Reported. The honest
          // signal is the COUNT — not a guess at whether a face is visible —
          // and this step is optional, so it lets the user skip the cheap ones
          // knowingly instead of paying for every name in the script.
          shotCount: 1,
        });
      }
    }
    return [...byKey.values()];
  }

  // WHAT THE BOARD IS TOLD ITS PEOPLE AND PLACES LOOK LIKE.
  //
  // computeCast() answers a different question — "who needs a reference image
  // drawn?" — so it keeps only the characters a shot names exactly, and gives
  // an empty description to anyone it can't pair up. That is right for the cast
  // step and wrong for the bible: an empty description tells the image model
  // nothing, and it goes back to inventing a new face per panel.
  //
  // So: everyone the breakdown described, plus anyone a shot names who wasn't
  // described. The server pairs shot names to cast entries tolerantly and only
  // ever mentions the characters actually in a given panel.
  function castForBible() {
    const out = characters.filter((c) => (c?.name || "").trim());
    const seen = new Set(out.map((c) => c.name.trim().toLowerCase()));
    for (const c of computeCast()) {
      const key = (c?.name || "").trim().toLowerCase();
      if (key && !seen.has(key)) {
        seen.add(key);
        out.push(c);
      }
    }
    return out;
  }

  // Same idea for props and locations. computeAssets() already starts from the
  // full breakdown list, so this only guards against a nameless entry.
  function assetsForBible() {
    return computeAssets().filter((a) => (a?.name || "").trim());
  }

  // The ACTIVE assets to lock = the breakdown's canonical prop/background list,
  // plus any asset named in a shot that wasn't in that list. Category/description
  // come from the canonical list when available.
  function computeAssets() {
    const metaByName = new Map(
      assets.map((a) => [a.name.trim().toLowerCase(), a])
    );
    const seen = new Set();
    const out = [];
    for (const a of assets) {
      const key = (a.name || "").trim().toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(a);
    }
    for (const sh of shots) {
      for (const raw of sh.assets || []) {
        const name = (raw || "").trim();
        const key = name.toLowerCase();
        if (!key || seen.has(key)) continue;
        seen.add(key);
        out.push(metaByName.get(key) || { name, category: "prop", description: "" });
      }
    }
    return out;
  }

  // What the current board was drawn from. If this still matches the board's
  // saved signature, the panels on screen are still valid — no need to redraw.
  function currentSig() {
    return JSON.stringify({
      shots,
      style: effectiveStyle(),
      aspect: effectiveAspect(),
      // Editing the world changes every panel, so it must invalidate the board.
      world,
      // ⚠ AND SO DOES THE AUDIENCE. Switching from India to the US changes the
      // money on every price tag and the language on every sign; a board left
      // marked "up to date" through that would show the old market's film.
      market: effectiveMarket(),
      // ⚠ AND THE BRAND. Swapping the logo file, or removing it, changes every
      // panel that shows the mark — a board still marked "up to date" through
      // that would be carrying the previous brand's film.
      brand: effectiveBrand(),
    });
  }
  // True when a board exists and nothing that affects the panels has changed.
  const boardUpToDate = Boolean(jobId) && generatedSig === currentSig();

  useEffect(() => {
    if (!sigStampJob || sigStampJob !== jobId) return;
    setSigStampJob(null);
    setGeneratedSig(currentSig());
    // ⚠ Keyed on the ARMING value, not on everything the signature reads.
    // Listing shots/world/style here would re-stamp the board as "up to date"
    // after a real edit, which is the opposite of what the signature is for.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sigStampJob, jobId]);

  // Review → cast → assets → board, skipping any step with nothing to set up.
  // When the board is already up to date, just reopen it (unless the user
  // explicitly asks to regenerate) so their drawn panels aren't thrown away.
  function handleReviewNext(forceRegen = false) {
    if (shots.length === 0 || busy) return;
    setError("");
    setNotice("");
    if (!forceRegen && boardUpToDate) {
      setStep("board");
      return;
    }
    // Rough Sketch draws straight from the prompts — no cast, no props page.
    if (skipsRefs()) {
      requestLaunch({}, {});
    } else if (computeCast().length > 0) {
      setStep("cast");
    } else if (computeAssets().length > 0) {
      setStep("assets");
    } else {
      requestLaunch(characterRefs || {}, {});
    }
  }

  // Cast done → assets step (if any) or straight to generation.
  function handleCastNext(charRefs) {
    setCharacterRefs(charRefs || {});
    if (computeAssets().length > 0) {
      setStep("assets");
    } else {
      requestLaunch(charRefs || {}, {});
    }
  }

  // Everything is chosen — ask for confirmation instead of generating. The modal
  // is the ONLY thing that calls startStoryboard, so no path can spend
  // generations without the user seeing what is about to be drawn.
  function requestLaunch(charRefs, assetRefs) {
    setError("");
    setNotice("");
    setPreflight({ charRefs: charRefs || {}, assetRefs: assetRefs || {} });
  }

  // Kick off panel generation with the chosen character + asset references.
  async function startStoryboard(charRefs, assetRefs) {
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      // A reference-free style sends NO refs, even if the user set some up and
      // then switched style in the pre-flight modal — otherwise the modal's
      // "not used by this style" would be a lie.
      if (skipsRefs()) {
        charRefs = {};
        assetRefs = {};
      }
      // Which of the chosen assets are props vs. backgrounds — the props step
      // doesn't report this, so read it back off the active asset list. Only
      // used to sort the downloadable ZIP into props/ and backgrounds/.
      const assetCategories = {};
      for (const a of computeAssets()) {
        if (assetRefs?.[a.name]) assetCategories[a.name] = a.category || "prop";
      }
      const res = await api.createStoryboard({
        shots,
        style: effectiveStyle(),
        aspectRatio: effectiveAspect(),
        // Saved with the job so the library card can name and label the board.
        title: effectiveTitle(),
        genre: effectiveGenre(),
        // The cast and props WITH their descriptions — the written continuity
        // bible that goes into every panel prompt. Sent even when this style
        // skips the cast/props steps (Rough Sketch does): those steps produce
        // reference IMAGES, and skipping them is no reason for the board to
        // forget what its own characters look like.
        //
        // The FULL breakdown lists, not computeCast()/computeAssets(). Those
        // two keep only entries whose name a shot spells exactly, so a shot
        // saying "Lead Thug" against a cast entry called "Thug Leader" would
        // arrive with an empty description — losing the one thing the bible is
        // for. The server matches names tolerantly, so give it everything and
        // let it pair them up; a panel is only ever told about the characters
        // that are actually in it.
        characters: castForBible(),
        assets: assetsForBible(),
        characterRefs: charRefs || {},
        assetRefs: assetRefs || {},
        assetCategories,
        world,
        market: effectiveMarket(),
        brand: effectiveBrand(),
        script: scriptText,
        // Promote the draft this board was reviewed as, rather than leaving it
        // behind as a second record of the same work.
        draftJobId,
      });
      setJobId(res.job_id);
      // The draft has become the board — stop autosaving to it (further PATCHes
      // would 409) and stop offering it as something to resume.
      setDraftJobId(null);
      setReviewSavedAt(null);
      setGeneratedSig(currentSig()); // remember what this board was drawn from
      setBoardOrigin("review");
      setPreflight(null); // confirmed and away — drop the modal
      setStep("board");
    } catch (e) {
      // Keep the modal up with the error in it: the chosen references are still
      // in `preflight`, so the user can just press Generate again. Behind it,
      // review is where Cancel should land.
      setError(e.message);
      setStep("review");
    } finally {
      setBusy(false);
    }
  }

  // Wipe the in-flight storyboard so the form / library starts clean.
  function resetWorkflow() {
    // Starting over means the saved draft is dead — discard it server-side too,
    // or it would be offered back on the next load. Fire-and-forget: failing to
    // delete it must not block the user from starting again.
    if (draftJobId) {
      api.discardStoryboardDraft(draftJobId).catch(() => {});
    }
    setDraftJobId(null);
    setReviewSavedAt(null);
    reviewLastSaved.current = "";
    setJobId(null);
    setGeneratedSig(null);
    setShots([]);
    setCharacters([]);
    setAssets([]);
    setWorld({});
    setScriptText("");
    setCharacterRefs({});
    setPreflight(null);
    clearSavedRefs();
    setScript("");
    setFile(null);
    setTitle("");
    setGenre("default");
    setCustomGenre("");
    setStyle(DEFAULT_STYLE);
    setCustomStyle("");
    setAspect(DEFAULT_ASPECT);
    setCustomAspect("");
    setError("");
    setNotice("");
    setIntake(null);
    setConcept(null);
    setConceptSource("");
  }

  // The pre-flight confirmation. Built once and dropped into every step that
  // can launch a generation (review / cast / props) — it's a fixed overlay, so
  // it renders over whichever of them is on screen.
  const preflightModal = preflight ? (
    <PreflightModal
      title={effectiveTitle()}
      shots={shots}
      cast={computeCast()}
      assets={computeAssets()}
      refsSkipped={skipsRefs()}
      charRefs={preflight.charRefs}
      assetRefs={preflight.assetRefs}
      savedCast={savedCastRefs}
      savedAssets={savedAssetRefs}
      styleOptions={ALL_STYLES}
      aspectOptions={ASPECTS}
      genreOptions={ALL_GENRES}
      style={style}
      customStyle={customStyle}
      onStyle={setStyle}
      onCustomStyle={setCustomStyle}
      aspect={aspect}
      customAspect={customAspect}
      onAspect={setAspect}
      onCustomAspect={setCustomAspect}
      genre={genre}
      customGenre={customGenre}
      onGenre={setGenre}
      onCustomGenre={setCustomGenre}
      world={world}
      onWorld={setWorld}
      busy={busy}
      error={error}
      onEditShot={updateShot}
      onCancel={() => {
        if (busy) return; // never abandon a launch that's already in flight
        setPreflight(null);
        setStep("review");
      }}
      onConfirm={() => startStoryboard(preflight.charRefs, preflight.assetRefs)}
    />
  ) : null;

  // =========================================================== Library step
  if (step === "library") {
    return (
      <StoryboardLibrary
        /* ⚠ THE UNFINISHED BOARD LIVES HERE NOW, and only here. It used to
           have a strip on the dashboard as well; asked to keep it in ONE place,
           and this is the page that lists what it will become. Resuming is a
           read of the same draft the form's banner offers. */
        onResume={(draft) => resumeDraft(draft)}
        onNew={() => {
          resetWorkflow();
          setStep("form");
        }}
        onOpen={async (board) => {
          // ⚠ RE-OPENING LOADS THE WORK, NOT JUST THE PICTURES. It used to
          // restore the display settings and the job id and nothing else, so
          // the review, cast and props steps had no content — and ← Back was
          // wired to the library for exactly that reason (see `boardOrigin`).
          // Reported: *"recent se khola to direct last page pe chala jata hun,
          // beech ka page nahi aa raha hai"*. The panels were reachable and
          // everything they were made from was not.
          setTitle(board.title || "");
          applySavedSettings(board);
          setJobId(board.job_id);
          setStep("board");
          try {
            const p = await api.getStoryboardProject(board.job_id);
            setShots(p.shots || []);
            setCharacters(p.characters || []);
            setAssets(p.assets || []);
            setWorld(p.world || {});
            setScriptText(p.script || "");
            // ⚠ AND THE PICTURES ALREADY PAID FOR. Without this the cast page
            // opens on four empty cards and a "Generate panels (skip refs)"
            // button — every reference the user bought, invisible, and the only
            // offered way forward is to buy them again. Same restore the draft
            // path has done since that was reported on drafts.
            if (p.character_refs) setCharacterRefs(p.character_refs);
            restoreSavedRefs(setSavedCastRefs, p.character_refs, null);
            restoreSavedRefs(setSavedAssetRefs, p.asset_refs, null);
            // Now the middle steps have something to show, so ← goes back one
            // step instead of all the way out.
            setBoardOrigin("review");
            setSigStampJob(board.job_id);
          } catch {
            // ⚠ FAILS SOFT, BACK TO EXACTLY THE OLD BEHAVIOUR. The panels are
            // already on screen and are what the user asked for; a dead lookup
            // must not take them away. ← keeps going to the library, because
            // that is still the only place with content.
            setBoardOrigin("library");
          }
        }}
        onDuplicate={(project) => {
          // Reuse the saved shots instead of re-running the paid breakdown —
          // drops the user straight on the review step with a fresh name.
          resetWorkflow();
          applySavedSettings(project);
          setShots(project.shots || []);
          setWorld(project.world || {});
          setScriptText(project.script || "");
          setTitle(`${project.title} (copy)`);
          setStep("review");
        }}
      />
    );
  }

  // ============================================================== Cast step
  if (step === "cast") {
    return (
      <>
        <StoryboardCast
          characters={computeCast()}
          saved={savedCastRefs}
          onSave={(name, fields) => saveRefFields(setSavedCastRefs, name, fields)}
          world={world}
          // ⚠ THE SAME STYLE THE PANELS WILL BE DRAWN IN. A cast sheet is a
          // look reference for every panel its character appears in, so this
          // has to be the board's real style — not left out, which is what made
          // every sheet a Pixar cartoon regardless of what was picked.
          style={effectiveStyle()}
          // The sheet shows no prices, but the audience still says who these
          // people are and what language is on anything they carry.
          market={effectiveMarket()}
          busy={busy}
          onBack={() => setStep("review")}
          onGenerate={handleCastNext}
        />
        {preflightModal}
      </>
    );
  }

  // ============================================================ Assets step
  if (step === "assets") {
    return (
      <>
        <StoryboardAssets
          assets={computeAssets()}
          saved={savedAssetRefs}
          onSave={(name, fields) => saveRefFields(setSavedAssetRefs, name, fields)}
          world={world}
          // ⚠ THIS IS THE STEP THAT MATTERS MOST. A prop is a phone, a menu, a
          // price tag; the reference is drawn once and baked into every panel
          // the object appears in, so the wrong currency here is the wrong
          // currency on the whole board.
          market={effectiveMarket()}
          busy={busy}
          onBack={() => setStep(computeCast().length > 0 ? "cast" : "review")}
          onGenerate={(assetRefs) => requestLaunch(characterRefs, assetRefs)}
        />
        {preflightModal}
      </>
    );
  }

  // ============================================================= Board step
  if (step === "board" && jobId) {
    return (
      <StoryboardBoard
        jobId={jobId}
        styleLabel={styleLabel}
        aspect={effectiveAspect()}
        backLabel={boardOrigin === "library" ? "Your Storyboards" : "Back to shots"}
        onBack={() => setStep(boardOrigin)}
        onOpenAnimatic={onOpenAnimatic}
      />
    );
  }

  // ============================================================ Review step
  // =========================================================== Concept step
  // ⚠ THE APPROVAL GATE, AND THE ONLY REASON THIS PHASE EXISTS. A brief or an
  // idea cannot become a storyboard without somebody inventing a film — who is
  // on screen, what goes wrong, how it ends. Before this screen the app made
  // those decisions in silence and the user met them as twenty finished,
  // paid-for drawings. Now they are thirty seconds of reading and one click,
  // and every one of them is editable first.
  //
  // A SCRIPT NEVER COMES THROUGH HERE. When the user wrote the thing there is
  // nothing to interpret, so `script_intake` sends it straight to the
  // breakdown — being asked to approve our reading of your own script would be
  // a step that exists only to annoy.
  if (step === "concept" && concept) {
    // ⚠ TWO WAITS LIVE ON THIS SCREEN, BACK TO BACK. Approving runs
    // write_script() AND THEN the breakdown, and the breakdown's own ring is
    // rendered by the `form` step — which this branch returns before ever
    // reaching. Without the second case below, the script ring reached 100%,
    // handed off to startBreakdown(), and the user was dropped back onto the
    // concept card with a live API call running and nothing on screen saying
    // so. It read as "I pressed Approve and it did nothing."
    //
    // ⚠ AND THEY SHARE ONE RING — ONE ELEMENT, MOUNTED ONCE, WITH ITS PROPS
    // CHANGING UNDER IT. Not two rings in a ternary and not two `key`s: either
    // of those is a second instance, which starts its own climb from zero and
    // is exactly what was reported twice over — *"progress bar pehle 100% ho
    // jaye fir kuch time pe open ho"*, and then *"kabhi fast kabhi slow"* when
    // the two were given half the bar each. The number now only ever goes up,
    // at one pace, and reaches 100 once.
    //
    // `final={!writing}` is the whole trick: while the script is being written
    // the ring hands off WHERE IT STANDS when that call returns, with no sprint
    // to a ceiling, and the breakdown carries on from the same number. Only the
    // breakdown — the last call before the review step — earns the sweep to
    // 100. See the note at the top of BreakdownProgress.jsx.
    if (writing || busy) {
      return (
        <div className="workflow-head-wrap sb-form">
          <div className="sts-form-wrap">
            <BreakdownProgress
              done={writing ? scriptWritten : breakdownDone}
              final={!writing}
              onDone={writing ? finishScript : finishBreakdown}
              steps={writing ? SCRIPT_STEPS : undefined}
              title={writing ? "Writing your script" : undefined}
              readyLabel={writing ? "Script ready!" : undefined}
              slowLabel={
                writing
                  ? "Still writing — a longer film takes a little more time…"
                  : undefined
              }
            />
          </div>
        </div>
      );
    }

    const scenes = concept.key_scenes || [];
    return (
      <div className="workflow-head-wrap sb-form">
        <div className="workflow-header">
          <button
            type="button"
            className="btn back-btn wf-back"
            title="Back"
            aria-label="Back"
            onClick={() => {
              setError("");
              setStep("form");
            }}
          >
            ←
          </button>
          <span className="wf-icon"><WorkflowIcon id="script-to-storyboard" /></span>
          <div>
            <h1 className="wf-title">Is this the right direction?</h1>
            <p className="muted">
              You gave us {intakeKindWord()}, so we had to work out the film.
              Change anything here — nothing is drawn until you approve it.
            </p>
          </div>
        </div>

        {error && <div className="error">{error}</div>}

        <div className="sts-form-wrap">
          <div className="card sts-concept">
            <label>Title</label>
            <input
              value={concept.title || ""}
              placeholder="A few words naming the film"
              maxLength={120}
              onChange={(e) => updateConcept({ title: e.target.value })}
            />

            <label>Core idea</label>
            <textarea
              className="prompt-textarea sts-concept-premise"
              value={concept.premise || ""}
              placeholder="What the film is, in a sentence or two"
              onChange={(e) => updateConcept({ premise: e.target.value })}
            />

            <label>
              Story direction{" "}
              <span className="label-optional">· the shape of the film</span>
            </label>
            <textarea
              className="prompt-textarea sts-concept-arc"
              value={concept.story_direction || ""}
              placeholder="Beginning → what changes → how it ends"
              onChange={(e) => updateConcept({ story_direction: e.target.value })}
            />

            {/* ⚠ THE SCENES ARE THE PART WORTH READING. Everything above is
                framing; this is what the board will actually be made of, which
                is why each one is its own editable line rather than a blob. */}
            <label>
              Key scenes{" "}
              <span className="label-optional">· in order</span>
            </label>
            <ol className="sts-concept-scenes">
              {scenes.map((sc, i) => (
                <li key={i}>
                  <span className="sts-concept-num">{i + 1}</span>
                  <input
                    value={sc}
                    placeholder="A moment we can see"
                    onChange={(e) => updateKeyScene(i, e.target.value)}
                  />
                  {/* The same two controls the shot cards carry, with the same
                      titles — there is one way to reorder a list in this app. */}
                  <button
                    type="button"
                    className="btn ghost small"
                    title="Move up"
                    disabled={i === 0}
                    onClick={() => moveKeyScene(i, -1)}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="btn ghost small"
                    title="Move down"
                    disabled={i === scenes.length - 1}
                    onClick={() => moveKeyScene(i, 1)}
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    className="btn ghost small"
                    title="Remove this scene"
                    onClick={() =>
                      updateConcept({
                        key_scenes: scenes.filter((_, idx) => idx !== i),
                      })
                    }
                  >
                    ✕
                  </button>
                </li>
              ))}
            </ol>
            <button
              type="button"
              className="btn ghost small"
              onClick={() => updateConcept({ key_scenes: [...scenes, ""] })}
            >
              ＋ Add a scene
            </button>

            <div className="sts-concept-row">
              <div>
                <label>Length</label>
                <div className="sts-concept-seconds">
                  <input
                    type="number"
                    min={5}
                    max={600}
                    value={concept.duration_seconds || 60}
                    onChange={(e) =>
                      updateConcept({
                        duration_seconds: Number(e.target.value) || 0,
                      })
                    }
                  />
                  <span className="tiny muted">seconds</span>
                </div>
              </div>
              <div className="sts-concept-look">
                <label>Look and feel</label>
                <input
                  value={concept.visual_direction || ""}
                  placeholder="e.g. premium, modern, uncluttered"
                  maxLength={160}
                  onChange={(e) =>
                    updateConcept({ visual_direction: e.target.value })
                  }
                />
              </div>
            </div>

            <div className="sts-concept-actions">
              <button
                type="button"
                className="btn primary"
                disabled={!conceptReady()}
                onClick={approveConcept}
              >
                ✓ Approve &amp; create storyboard
              </button>
              <button
                type="button"
                className="btn ghost"
                onClick={() => {
                  setError("");
                  setConcept(null);
                  setStep("form");
                }}
              >
                Start over
              </button>
            </div>
            {!conceptReady() && (
              <p className="tiny muted sts-concept-hint">
                Add a core idea or at least one scene before approving.
              </p>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (step === "review") {
    const activeCast = computeCast();
    const activeAssets = computeAssets();
    return (
      <div className="workflow-head-wrap sb-review">
        <div className="workflow-header">
          {/* Back leads the header row, in the same box as the icon beside it —
              see `.wf-back` in shell.css. */}
          <button
            type="button"
            className="btn back-btn wf-back"
            title="Back"
            aria-label="Back"
            onClick={() => {
              setNotice("");
              setError("");
              setStep("form");
            }}
          >
            ←
          </button>
          <span className="wf-icon"><WorkflowIcon id="script-to-storyboard" /></span>
          <div>
            <h1 className="wf-title">Review your shots</h1>
            <p className="muted">
              Edit, reorder or delete panels before generating — this is your
              chance to fix the AI before it draws.
            </p>
            {/* This step used to be lost on refresh, and the breakdown behind it
                had already cost quota. Say plainly that it's safe now. */}
            {reviewSavedAt && (
              <p className="sts-draft-status sts-draft-inline" title={reviewSavedAt}>
                ✓ Saved — you can close this and come back to it
              </p>
            )}
          </div>
        </div>

        <div className="review-actions top-actions">
          <div className="review-actions-right">
            {/* Board already drawn from these exact shots → offer to reopen it
                (keeping the panels) plus a separate Regenerate. Editing any shot
                makes boardUpToDate false and this collapses back to one button. */}
            {boardUpToDate && !busy ? (
              <>
                {/* ⚠ THE WAY FORWARD HAS TO SURVIVE THE BOARD EXISTING. Once
                    the panels matched the shots, this row collapsed to
                    Regenerate + Back — and the cast and props steps became
                    unreachable, which is exactly when they are needed most: a
                    character or a prop that came out wrong is FIXED on those
                    screens. Reported after a finished board, with no route to
                    the props step to lock the idol that was drifting.

                    ⚠ It goes to the FIRST step that has anything on it, so the
                    button never opens an empty screen. Nothing is spent by
                    arriving — both steps generate only when asked. */}
                {!skipsRefs() && (activeCast.length > 0 || activeAssets.length > 0) && (
                  <button
                    type="button"
                    className="btn"
                    onClick={() => setStep(activeCast.length > 0 ? "cast" : "assets")}
                    title="Edit or redraw the character and prop references this board was built from"
                  >
                    🎭 Cast &amp; props
                  </button>
                )}
                <button
                  type="button"
                  className="btn"
                  onClick={() => handleReviewNext(true)}
                  title="Draw every panel again from scratch"
                >
                  🔄 Regenerate
                </button>
                <button
                  type="button"
                  className="btn primary"
                  onClick={() => handleReviewNext(false)}
                >
                  → Back to your storyboard
                </button>
              </>
            ) : (
              <button
                type="button"
                className="btn primary"
                disabled={shots.length === 0 || busy}
                onClick={() => handleReviewNext(false)}
              >
                {busy ? (
                  <>
                    <span className="spinner-inline" /> Starting…
                  </>
                ) : skipsRefs() ? (
                  /* Rough Sketch has no cast/props step to offer. */
                  `🎬 Generate panels (${shots.length})`
                ) : activeCast.length > 0 ? (
                  `🎭 Next: cast (${activeCast.length})`
                ) : activeAssets.length > 0 ? (
                  `🎬 Next: props (${activeAssets.length})`
                ) : (
                  `🎬 Generate panels (${shots.length})`
                )}
              </button>
            )}
          </div>
        </div>

        {/* ⚠ THE RUNTIME IS WHY THE LENGTH FIELD EARNS ITS PLACE. Every other
            piece of shot metadata is read one shot at a time; this is the one
            number that is about the FILM, and it is the answer to the question
            behind most briefs — "is my 30-second ad actually 30 seconds?" */}
        <div className="review-summary">
          <span className="chip">{styleLabel}</span>
          <span className="chip">{effectiveAspect()}</span>
          <span className="chip">
            {shots.length} shot{shots.length === 1 ? "" : "s"}
          </span>
          {totalSeconds() > 0 && (
            <span
              /* ⚠ RED WHEN THE BOARD OVERSHOOTS WHAT WAS APPROVED. The
                 breakdown is now told the target, but it is a model being
                 argued with, not a clamp — so the one place the user can
                 catch a 30-second film that came back at 64 seconds is here,
                 before the panels are drawn and paid for. A fifth over is
                 the point where merging shots is worth the reader's time. */
              className={`chip${overRunning() ? " chip-warn" : ""}`}
              title={
                targetSeconds
                  ? `Added up from each shot's length. You approved ${formatRuntime(
                      targetSeconds
                    )} — shorten or merge shots to get closer.`
                  : "Added up from each shot's length"
              }
            >
              ≈ {formatRuntime(totalSeconds())}
              {targetSeconds ? ` of ${formatRuntime(targetSeconds)}` : ""}
            </span>
          )}
        </div>

        {error && <div className="error">{error}</div>}
        {notice && <div className="info-msg">{notice}</div>}

        {/* The world every reference and panel gets drawn in — check it BEFORE
            generating the cast, since that's the first thing it affects. */}
        <WorldSetting world={world} onChange={setWorld} />

        {/* The whole script, numbered — this is what each shot's "LINE n" points at. */}
        <ScriptPanel script={scriptText} />

        {/* Every speaker field autocompletes against the cast, so a line
            re-attributed by hand keeps the name spelling the panels use. */}
        <datalist id="sb-cast-names">
          {computeCast().map((c) => (
            <option key={c.name} value={c.name} />
          ))}
        </datalist>

        <div className="shot-list">
          {shots.map((sh, i) => (
            /* A full-width divider wherever the scene changes, so the script's
               scene breaks are visible instead of only implied by a tag. */
            <Fragment key={i}>
              {sh.scene_number !== shots[i - 1]?.scene_number && (
                <div className="scene-divider">
                  <span className="scene-divider-label">
                    Scene {sh.scene_number}
                  </span>
                  {sh.location && (
                    <span className="scene-divider-where">{sh.location}</span>
                  )}
                  <span className="scene-divider-count">
                    {shots.filter((s) => s.scene_number === sh.scene_number).length} shot
                    {shots.filter((s) => s.scene_number === sh.scene_number).length === 1
                      ? ""
                      : "s"}
                  </span>
                </div>
              )}
            <div className="card shot-card">
              <div className="shot-head">
                <span className="shot-index">
                  <span className="shot-scene">Scene {sh.scene_number} ·</span>
                  Shot {sceneShotNo(shots, i)}
                </span>
                <div className="shot-actions">
                  <button
                    type="button"
                    className="shot-btn"
                    onClick={() => moveShot(i, -1)}
                    disabled={i === 0}
                    title="Move up"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="shot-btn"
                    onClick={() => moveShot(i, 1)}
                    disabled={i === shots.length - 1}
                    title="Move down"
                  >
                    ↓
                  </button>
                  <button
                    type="button"
                    className="shot-btn"
                    onClick={() => insertShot(i)}
                    title="Insert a shot below"
                  >
                    ＋
                  </button>
                  <button
                    type="button"
                    className="shot-btn danger"
                    onClick={() => deleteShot(i)}
                    title="Delete shot"
                  >
                    ✕
                  </button>
                </div>
              </div>

              {/* Their script first, then the AI's prompt for it. */}
              <ScriptLineBox shot={sh} />

              {/* ⚠ IT GROWS TO ITS TEXT, and that is not a nicety. This box
                  was a fixed 64px with its own scrollbar, so the prompt cut
                  off after two or three lines — while the read-only "FROM
                  YOUR SCRIPT" box directly above it showed every word. The
                  one thing on the card the user is meant to EDIT was the one
                  thing they could not see. */}
              <label className="shot-prompt-label">Image prompt</label>
              <GrowTextarea
                className="prompt-textarea shot-desc"
                value={sh.description}
                placeholder="Describe what we see in this panel…"
                onChange={(e) => updateShot(i, { description: e.target.value })}
              />

              {/* What is SAID in this shot. Empty for a silent shot, which
                  shows only the "＋ Add dialogue" link.
                  ⚠ ORDER: image prompt → dialogue → camera/location → cast.
                  The board tile and the PDF print the same order; a panel that
                  reads differently in three places reads as three tools. */}
              <DialogueEditor
                dialogue={sh.dialogue}
                characters={sh.characters}
                onChange={(dialogue) => updateShot(i, { dialogue })}
              />

              <div className="grid2 shot-meta">
                <div>
                  <label>Shot type</label>
                  <input
                    value={sh.camera}
                    placeholder="e.g. wide, close-up"
                    onChange={(e) => updateShot(i, { camera: e.target.value })}
                  />
                </div>
                <div>
                  <label>Location</label>
                  <input
                    value={sh.location}
                    placeholder="e.g. city street, night"
                    onChange={(e) => updateShot(i, { location: e.target.value })}
                  />
                </div>
              </div>

              {/* ⚠ NEITHER OF THESE REACHES THE IMAGE PROMPT, and that is not
                  an oversight. A still panel cannot show a camera move or a
                  length; asking a model for one gets motion blur, speed lines
                  or a little arrow drawn INTO the frame. They are read by the
                  board, the PDF and the animatic step — where motion and
                  timing are real — exactly as `dialogue` is. */}
              <div className="grid2 shot-meta">
                <div>
                  <label>Camera move</label>
                  <input
                    value={sh.movement || ""}
                    placeholder="e.g. static, slow push-in"
                    onChange={(e) => updateShot(i, { movement: e.target.value })}
                  />
                </div>
                <div>
                  <label>Length</label>
                  <div className="shot-seconds">
                    <input
                      type="number"
                      min={0}
                      max={30}
                      value={sh.duration_seconds ?? ""}
                      onChange={(e) =>
                        updateShot(i, {
                          duration_seconds: Number(e.target.value) || 0,
                        })
                      }
                    />
                    <span className="tiny muted">seconds</span>
                  </div>
                </div>
              </div>

              {/* ⚠ THE ONE FIELD THAT DECIDES WHETHER A PROP STAYS THE SAME
                  PROP, and until this input there was no way to type into it.
                  Found on the first finished board: the Ganesh idol — the
                  subject of the whole film, in nine of fifteen panels — was
                  drawn differently each time, because the breakdown returned an
                  EMPTY asset list and nothing downstream could add one. Every
                  character was consistent; each of them had a reference.

                  The chain runs entirely off these names:
                    this list → `computeAssets()` → the props step appears
                    → a reference is drawn → `_gather_refs()` matches the shot's
                      own names back to it → the panel is drawn holding it.
                  Break the first link and the other three are unreachable, and
                  the props step simply never opens.

                  ⚠ SPLIT ON THE COMMA AND NOTHING ELSE — no trim, no filter,
                  and joined back with a bare comma. Both were tried and both
                  broke typing, which `tests/workflow_mount_check.py` caught:
                  filtering the empty piece ate the comma the instant it was
                  typed (one name, for ever), and trimming each piece ate the
                  SPACE inside a name — "Ganesh idol" could only ever be typed
                  as "Ganeshidol". Split/join this way round-trips whatever was
                  typed, character for character. Cleaning belongs at the far
                  end, where it already happens: `computeAssets` and
                  `assetsForBible` trim and drop blanks, and `_gather_refs`
                  lower-cases and resolves aliases besides. */}
              <div className="shot-assets-row">
                <label>Props &amp; backgrounds</label>
                <input
                  value={(sh.assets || []).join(",")}
                  placeholder="e.g. Ganesh idol, puja room"
                  title="Name anything that must look the SAME in every shot it appears in — you can draw one reference for it on the props step"
                  onChange={(e) =>
                    updateShot(i, { assets: e.target.value.split(",") })
                  }
                />
              </div>

              {sh.characters?.length > 0 && (
                <div className="shot-chars">
                  {sh.characters.map((c, ci) => (
                    <span className="chip" key={ci}>
                      {c}
                    </span>
                  ))}
                </div>
              )}
            </div>
            </Fragment>
          ))}
        </div>

        <div className="add-shot-row">
          <button type="button" className="btn ghost add-shot-btn" onClick={addShot}>
            ＋ Add a shot
          </button>
        </div>

        {preflightModal}
      </div>
    );
  }

  // ============================================================== Form step
  return (
    <div className="workflow-head-wrap sb-form">
      {/* ⚠ BACK SITS IN THE HEADER ROW, AHEAD OF THE ICON. It used to be on a
          row of its own under the title — a lone arrow in an otherwise empty
          strip, and one more thing between the heading and the form. In the
          header it reads as what it is: the way out of this screen, at the
          start of the line that names the screen. */}
      <div className="workflow-header">
        <button
          type="button"
          className="btn back-btn wf-back"
          onClick={() => setStep("library")}
          title="Your Storyboards"
          aria-label="Your Storyboards"
        >
          ←
        </button>
        <span className="wf-icon"><WorkflowIcon id="script-to-storyboard" /></span>
        <div>
          <h1 className="wf-title">Script to Storyboard</h1>
          <p className="muted">
            Hand over your script — we read it, break it into shots and draw
            the board.
          </p>
        </div>
      </div>

      <div className={`sts-hero-grid ${busy ? "busy" : ""}`}>
      <div className="sts-form-wrap">
        {busy ? (
          <BreakdownProgress done={breakdownDone} onDone={finishBreakdown} />
        ) : (
        <div className="card">
          {/* --- Title (what this board is saved as in the library) --- */}
          <label>Storyboard title</label>
          <input
            value={title}
            placeholder="Leave blank to name it after your script's first line"
            maxLength={120}
            onChange={(e) => setTitle(e.target.value)}
          />

          {/* --- Source material -------------------------------------------
              ⚠ ONE BOX, ONE BUTTON. This was three controls: a "paste" tab, an
              "upload" tab, and a chat inside the paste tab with a Generate
              button of its own. Every one of them ended at the same `script`
              state, so the only thing the choice added was the question "which
              one am I supposed to use?" — asked before the user had done
              anything at all.

              The chat has not been deleted, it has been MOVED IN TIME: talking
              to an assistant makes sense once there is a board to change, not
              while the user is still handing over what they already have. See
              ScriptChat.jsx, still in the tree and still routed.

              Upload is a button inside this same box rather than a tab beside
              it, because pasting a script and uploading one are the same act —
              handing over the source — and a file can also just be dropped on
              the box. */}
          <label className="sts-script-label">Your script, brief or idea</label>
          <div
            className={`sts-script-panel ${dragOver ? "over" : ""}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={handleDrop}
          >
            <textarea
              className="prompt-textarea sts-script-area"
              placeholder="Paste your script, brief, story or idea — we'll work out what it is…"
              ref={scriptRef}
              value={script}
              onChange={(e) => {
                setScript(e.target.value);
                // The panel below is a verdict on text that no longer exists.
                if (intake) setIntake(null);
              }}
            />
            <div className="sts-script-status">
              {/* The left slot, finally used. This is the way BACK to a card
                  you pressed ← on: the concept is still in state, and without
                  this the only route to it is generating a new one. ⚠ It is
                  not how a card survives a refresh — that is the reopen in the
                  draft restore above, because "New storyboard" is the only
                  path to this screen from cold and it clears the concept. */}
              {concept && conceptReady() ? (
                <button
                  type="button"
                  className="linklike"
                  title="Pick up the concept you were reading — nothing has been drawn yet"
                  onClick={() => setStep("concept")}
                >
                  ↩ Resume your concept
                </button>
              ) : (
                <span />
              )}
              {/* Quiet confirmation that the typing is safe. Only appears once
                  something has actually been saved — an idle "not saved" badge
                  on an empty box is noise. */}
              {draftSavedAt && (
                <span className="sts-draft-status" title={draftSavedAt}>
                  {script === draftLastSaved.current
                    ? "✓ Draft saved"
                    : "Saving…"}
                </span>
              )}
            </div>

            <div className="sts-source-foot">
              {/* ⚠ NOT `ghost` — see `.sts-source-foot .btn` in storyboard.css.
                  Borderless on the panel's own grey, this read as a caption
                  printed inside the box instead of the second way of handing
                  over a script. */}
              <button
                type="button"
                className="btn small"
                onClick={() => fileInputRef.current?.click()}
              >
                📁 Upload a script file
              </button>
              {file ? (
                <span className="sts-file-chip">
                  📄 {file.name}
                  <button
                    type="button"
                    className="linklike"
                    onClick={() => setFile(null)}
                  >
                    Remove
                  </button>
                </span>
              ) : (
                <span className="tiny muted">
                  or drop it here · TXT / Fountain / FDX
                </span>
              )}
            </div>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.txt,.fountain,.fdx,.docx,.md"
            hidden
            onChange={(e) => pickFile(e.target.files?.[0])}
          />

          {/* --- What we made of it ------------------------------------------
              ⚠ THE ONE THING THIS FORM USED TO NEVER SAY. Every box of text
              went straight into the breakdown as a script, so an idea came back
              as an invented film — drawn, charged for, and never shown to
              anyone as a decision. This panel is where the app admits what it
              thinks it was handed, BEFORE it spends anything on pictures.

              It appears only when the answer is not "script": a script goes
              through in silence, because being told your script is a script is
              not information. */}
          {intake && (
            <div className={`sts-intake sts-intake-${intake.kind}`}>
              {/* ⚠ ONLY 'empty' AND 'vague' LAND HERE NOW. A brief or an idea
                  used to get a warning and a "Build it anyway" button; since
                  Phase 3 they go to the concept step instead, where the
                  invention is shown and approved rather than waved through. */}
              <p className="sts-intake-title">
                {intake.kind === "empty"
                  ? "Let's start with an idea."
                  : "Tell us a bit more."}
              </p>

              {/* The model's own sentence, in the user's own language.
                  ⚠ NOT SHOWN FOR 'vague': the question below already says the
                  same thing and says it usefully, and stacking "this is a wish
                  with no subject" on top of it reads as a telling-off. */}
              {intake.reason && intake.kind !== "vague" && (
                <p className="sts-intake-body">{intake.reason}</p>
              )}

              {intake.kind === "empty" && (
                <p className="sts-intake-body">
                  What would you like to create? Paste a script, describe your
                  idea, or just say what the film should do.
                </p>
              )}

              {intake.kind === "vague" && (
                <>
                  {/* ⚠ ONE QUESTION. Ten questions at once is an interrogation,
                      and the answer to it is a closed tab. */}
                  <p className="sts-intake-body">
                    {intake.question ||
                      "What would you like the story to be about?"}
                  </p>
                  <p className="tiny muted">
                    You can describe the characters, the situation, the product
                    — or just the basic idea.
                  </p>
                </>
              )}

            </div>
          )}

          {/* --- Audience ---
              ⚠ ONE DROPDOWN, AND IT USED TO BE TWO. The country picker beside
              this one asked, in effect, "what money do you want?" — a question
              nobody making a film about two friends on a train has an answer
              to, and a strange thing to meet on the way to a storyboard. The
              country is worked out from this language, the account default or
              the script instead (market.py), so the money still comes out right
              and the form asks one thing rather than two.

              A dropdown and not chips: twenty-five languages in a chip row
              would bury the Genre and Style rows that matter on every board.
              It wears the same pill the board's "Add a style" select does. */}
          {/* --- Brand ---
              ⚠ THE LOGO IS UPLOADED, NEVER GENERATED. An image model rebuilds a
              mark from its description every time it draws one, so four panels
              of one brand come back as four different logos. With a file here
              the model draws a flat placeholder and the server pastes this
              exact PNG in; with none, it is told to invent nothing at all.

              ⚠ THAT USED TO BE SPELLED OUT IN A LINE UNDER THE ROW AND IS NOT
              ANY MORE — "no logo uploaded, we never invent one" explained our
              engineering to somebody who only wanted to draw a storyboard. An
              upload slot marked "No logo" already says what it wants. */}
          {/* ⚠ ONE ROW, NOT TWO. The language dropdown is a short pill and
              the brand row was landing under a half-empty line — the logo
              slot belongs beside the language, not below it. Both columns
              wear the same label + control rhythm, so they line up. */}
          <div className="sts-meta-row">
            <div className="sts-meta-col sts-meta-audience">
            <label title="The language on screens and signs">Audience</label>
            <div className="opt-chips sts-audience">
              <select
                className="opt-select"
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                title="The language written on screens and signs"
              >
                {MARKET_LANGUAGES.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.id ? l.label : "Auto — from your script"}
                  </option>
                ))}
              </select>
            </div>
            </div>
            <div className="sts-meta-col sts-meta-brand">
            <label title="Only if this film sells a product">Brand</label>
            <div className="sts-brand">
              <input
                className="sts-brand-name"
                value={brandName}
                placeholder="Brand or app name, e.g. Lickyeat"
                maxLength={80}
                onChange={(e) => setBrandName(e.target.value)}
              />
              <div className="sts-brand-logo">
                {brandLogoPreview ? (
                  <img
                    src={brandLogoPreview}
                    alt="Your logo"
                    className="sts-brand-preview"
                  />
                ) : (
                  <span className="sts-brand-empty">No logo</span>
                )}
                <input
                  ref={brandFileRef}
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  hidden
                  onChange={(e) => {
                    uploadLogo(e.target.files?.[0]);
                    e.target.value = ""; // allow re-selecting the same file
                  }}
                />
                {/* ⚠ NOT `ghost`. A borderless button between two bordered
                    fields reads as a link that wandered into a form row — see
                    the `.sts-meta-row` note in storyboard.css. Both buttons in
                    this row wear `.btn.small` and the row's own height. */}
                <button
                  type="button"
                  className="btn small"
                  disabled={brandBusy}
                  onClick={() => brandFileRef.current?.click()}
                >
                  {brandBusy ? (
                    <>
                      <span className="spinner-inline" /> Uploading…
                    </>
                  ) : brandLogoId ? (
                    "Replace logo"
                  ) : (
                    "📁 Upload logo"
                  )}
                </button>
                {brandLogoId && (
                  <button type="button" className="btn small" onClick={clearLogo}>
                    Remove
                  </button>
                )}
              </div>
            </div>
            </div>
          </div>
          {brandError && <div className="error">{brandError}</div>}

        </div>
        )}
      </div>

      {!busy && (
      <aside className="sts-hero-aside">
        <div className="card sts-options">
          <h3 className="sts-options-title">Story settings</h3>
          {/* --- Genre --- */}
          {/* ⚠ HINTS LIVE ON HOVER, NOT ON THE PAGE. Every chip and label used to
              carry its explanation inline and the panel read as a wall of grey
              text over the choices themselves. The words are all still here —
              in `title`, the same way the aspect chips have always done it — so
              a mouse gets the answer and the eye gets a clean row. Keep any new
              one to 3-5 words; a paragraph in a tooltip is just as unreadable. */}
          <label title="Shapes the tone">Genre</label>
          <div className="opt-chips">
            {GENRES.map((g) => (
              <button
                key={g.id}
                type="button"
                className={`opt-chip ${genre === g.id ? "active" : ""}`}
                onClick={() => setGenre(g.id)}
                title={g.note}
              >
                {g.label}
              </button>
            ))}
            {/* Show the picked overflow genre so the selection stays visible. */}
            {MORE_GENRES.some((g) => g.id === genre) && (
              <button
                type="button"
                className="opt-chip active"
                onClick={() => setGenreMoreOpen(true)}
                title={ALL_GENRES.find((g) => g.id === genre)?.note}
              >
                {ALL_GENRES.find((g) => g.id === genre)?.label}
              </button>
            )}
            <button
              type="button"
              className="opt-chip opt-chip-more"
              onClick={() => setGenreMoreOpen(true)}
            >
              ＋ More
            </button>
          </div>
          {genre === "custom" && (
            <input
              className="custom-genre-input"
              value={customGenre}
              placeholder="Type your genre, e.g. Neo-noir, Coming-of-age…"
              onChange={(e) => setCustomGenre(e.target.value)}
            />
          )}

          {/* --- Style --- */}
          <label title="Hover a style to see it">Visual style</label>
          <div className="opt-chips">
            {STYLES.map((s) => (
              <button
                key={s.id}
                type="button"
                className={`opt-chip ${style === s.id ? "active" : ""}`}
                onClick={() => setStyle(s.id)}
                title={s.note}
              >
                {s.label}
              </button>
            ))}
            {/* Show the picked overflow style so the selection stays visible. */}
            {MORE_STYLES.some((s) => s.id === style) && (
              <button
                type="button"
                className="opt-chip active"
                onClick={() => setStyleMoreOpen(true)}
                title={ALL_STYLES.find((s) => s.id === style)?.note}
              >
                {ALL_STYLES.find((s) => s.id === style)?.label}
              </button>
            )}
            <button
              type="button"
              className="opt-chip opt-chip-more"
              onClick={() => setStyleMoreOpen(true)}
            >
              ＋ More
            </button>
          </div>
          {style === "custom" && (
            <input
              className="custom-genre-input"
              value={customStyle}
              placeholder="Describe your own style, e.g. 1980s retro anime, ink wash…"
              onChange={(e) => setCustomStyle(e.target.value)}
            />
          )}
          {/* ⚠ THE PARAGRAPH THAT USED TO SIT HERE IS GONE. Four lines of grey
              explanation under the style row pushed "Create storyboard" down
              the panel and got skipped anyway. What it said now rides on the
              chips themselves: "Cheapest — skips cast step" on Rough Sketch,
              and the one-line summary below on whichever style is picked. */}
          <p className="tiny muted style-note">
            {skipsRefs()
              ? "Quick grey pass — no cast step."
              : "Locks characters and sets first."}
          </p>

          {/* --- Aspect ratio --- */}
          <label>Aspect ratio</label>
          <div className="opt-chips">
            {ASPECTS.map((a) => (
              <button
                key={a.id}
                type="button"
                className={`opt-chip ${aspect === a.id ? "active" : ""}`}
                onClick={() => setAspect(a.id)}
                title={a.note}
              >
                {a.id}
                <span className="opt-chip-note">{a.note}</span>
              </button>
            ))}
            <button
              type="button"
              className={`opt-chip ${aspect === "custom" ? "active" : ""}`}
              onClick={() => setAspect("custom")}
            >
              ＋ Custom
            </button>
          </div>
          {aspect === "custom" && (
            <input
              className="custom-genre-input"
              value={customAspect}
              placeholder="Type a ratio, e.g. 4:3, 5:4, 1.85:1…"
              onChange={(e) => setCustomAspect(e.target.value)}
            />
          )}

          {error && <div className="error">{error}</div>}

          <button
            type="button"
            className="btn primary sts-generate"
            disabled={!canGenerate || busy || reading || developing}
            onClick={handleGenerate}
          >
            {busy ? (
              <span className="btn-loading">
                <span className="btn-ring" />
                Breaking down your script…
              </span>
            ) : reading ? (
              <span className="btn-loading">
                <span className="btn-ring" />
                Reading what you gave us…
              </span>
            ) : developing ? (
              <span className="btn-loading">
                <span className="btn-ring" />
                Working out the concept…
              </span>
            ) : (
              "🎬 Create storyboard"
            )}
          </button>
        </div>
      </aside>
      )}
      </div>

      {genreMoreOpen && (
        <MorePopup
          title="More genres"
          options={MORE_GENRES}
          selected={genre}
          onSelect={(id) => setGenre(id)}
          onClose={() => setGenreMoreOpen(false)}
        />
      )}
      {styleMoreOpen && (
        <MorePopup
          title="More visual styles"
          options={MORE_STYLES}
          selected={style}
          onSelect={(id) => setStyle(id)}
          onClose={() => setStyleMoreOpen(false)}
        />
      )}
    </div>
  );
}

// Overlay picker for the overflow ("＋ More") genres / styles. Selecting an
// option applies it and closes; the ✕ (or a backdrop click) closes without change.
function MorePopup({ title, options, selected, onSelect, onClose }) {
  return (
    <div className="more-overlay" onClick={onClose}>
      <div className="more-panel" onClick={(e) => e.stopPropagation()}>
        <div className="more-head">
          <h3>{title}</h3>
          <button type="button" className="more-close" onClick={onClose} title="Close">
            ✕
          </button>
        </div>
        <div className="opt-chips">
          {options.map((o) => (
            <button
              key={o.id}
              type="button"
              className={`opt-chip ${selected === o.id ? "active" : ""}`}
              onClick={() => {
                onSelect(o.id);
                onClose();
              }}
              title={o.note}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
