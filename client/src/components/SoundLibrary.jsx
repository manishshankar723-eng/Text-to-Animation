// SoundLibrary.jsx — the Sounds tab in the Media pane: somebody else's catalogue.
//
// It sits beside Media, Shapes and Effects for the reason those three are
// separate at all: this is a LIBRARY you take from, not a list of what this
// animatic contains. Media answers "what is in this film"; this answers "what
// could be".
//
// ⚠ IT IS THE FOURTH TAB, NOT A MODAL, on purpose. Picking music is something
// you do WHILE looking at the cut — you play four seconds, look at the shot,
// try another — and a dialog over the monitor takes away the only thing you are
// choosing against. Same reason Effects is a tab.
//
// ⚠ CLICK ADDS, DRAG DOES NOT. Every other library here is draggable, and this
// one deliberately is not: a shape and an effect exist already, whereas a sound
// has to be FETCHED from Freesound and written into the project before there is
// an upload id for a lane to accept. A drag whose payload is not ready until the
// drop has already happened is a drag that lies about what it is carrying, so
// the tile does the one honest thing — press it, wait, and it appears in Media
// and on the timeline. `onAdd` is async and this component shows the wait.
//
// ⚠ THE PREVIEW PLAYS OFF FREESOUND'S CDN, NOT THROUGH OUR SERVER. Their
// preview mp3s are public and unauthenticated, so the browser can have them
// directly — and a proxy would have to re-ask the API for the URL on every
// press, out of a budget of 60 requests a minute shared by every user of the
// deployment. IMPORTING goes through us: that is once per sound and has to be
// checked. See `freesound._normalise`.
//
// ⚠ ONE <audio> ELEMENT FOR THE WHOLE LIST. Pressing ▶ on a second row stops
// the first, because two sounds playing at once is not a comparison — and the
// editor's own transport may be running underneath as well.

import { useEffect, useRef, useState } from "react";

import * as api from "../api.js";
import Icon from "./Icon.jsx";

// What the licence picker offers. ⚠ THE ORDER IS THE ADVICE: CC0 first, because
// it is the only answer that puts no obligation on whoever exports the video.
// There is no fourth entry for NonCommercial and there must never be one — the
// server has no code for it either (see `freesound.LICENCES`).
const LICENCE_OPTIONS = [
  { id: "safe", label: "CC0 only — no credit needed", hint: "Public domain. Use it in anything, including paid work, and credit nobody." },
  { id: "credit", label: "CC BY — credit required", hint: "Free for commercial use too, but your finished video must name the author." },
  { id: "both", label: "CC0 + CC BY", hint: "Everything you are allowed to sell with. Check each card's badge." },
];

const SORT_OPTIONS = [
  { id: "relevance", label: "Best match" },
  { id: "downloads", label: "Most downloaded" },
  { id: "rating", label: "Highest rated" },
  { id: "newest", label: "Newest" },
  { id: "shortest", label: "Shortest first" },
  { id: "longest", label: "Longest first" },
];

// A starting point, so the tab is not an empty box on the first visit. These are
// typed into the search box for real — they are not a special "browse" mode —
// so what you get is exactly what typing them yourself would give.
const SUGGESTIONS = ["cinematic", "ambient loop", "whoosh", "footsteps", "rain", "ui click", "piano"];

// How long to wait after the last keystroke. ⚠ NOT A UI POLISH — a free
// Freesound key allows 60 requests a MINUTE for the whole deployment, so a
// search per keystroke spends everybody's budget on one person's typing.
const DEBOUNCE_MS = 500;

function seconds(ms) {
  const s = Math.max(0, Math.round(Number(ms) || 0) / 1000);
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.round(s - m * 60)).padStart(2, "0")}`;
}

/**
 * One result.
 *
 * `busy` is this row's own import, not the pane's: two adds in a row should
 * each show their own wait, and the second must not look finished because the
 * first one was.
 */
function SoundCard({ item, playing, onPlay, onAdd, busy, disabled }) {
  return (
    <li className={`snd-card ${playing ? "on" : ""}`}>
      <button
        type="button"
        className="snd-play"
        onClick={() => onPlay(item)}
        title={playing ? "Stop the preview" : "Play a preview"}
        aria-label={playing ? "Stop the preview" : "Play a preview"}
      >
        {/* The transport's own glyphs, deliberately — one editor, one play
            button, whatever it happens to be playing. */}
        {playing ? "❚❚" : "▶"}
      </button>

      <span className="snd-body">
        <span className="snd-line">
          <span className="snd-name" title={item.name}>{item.name}</span>
          {/* ⚠ THE BADGE IS THE OBLIGATION, NOT THE LICENCE NAME. "CC BY" means
              nothing to somebody who has not read the deed; "credit needed" is
              the thing they have to DO, and it is what makes the difference
              visible while choosing rather than after exporting. */}
          <span className={`snd-lic ${item.needs_credit ? "credit" : "free"}`}>
            {item.needs_credit ? "credit needed" : "no credit"}
          </span>
        </span>
        <span className="snd-line snd-sub">
          <span className="snd-len">{seconds(item.duration_ms)}</span>
          <span className="snd-by">by {item.username}</span>
          {/* The link a CC BY credit has to carry anyway, so it is also the
              honest "where did this come from" for a CC0 one. */}
          <a
            className="snd-src"
            href={item.page_url}
            target="_blank"
            rel="noreferrer noopener"
            title="Open this sound on freesound.org"
          >
            <Icon name="link" title="Open on Freesound" />
          </a>
        </span>
        {item.waveform_url ? (
          <img className="snd-wave" src={item.waveform_url} alt="" loading="lazy" />
        ) : null}
      </span>

      <button
        type="button"
        className="snd-add"
        onClick={() => onAdd(item)}
        disabled={busy || disabled}
        title={
          disabled
            ? "This project already holds the most audio tracks it can."
            : "Download it into this project and put it on an audio row"
        }
      >
        {busy ? "…" : "＋ Add"}
      </button>
    </li>
  );
}

/**
 * @param onAdd     async (item) => void — the editor imports it and makes the
 *                  track. Throwing is how it reports a failure; the message is
 *                  shown on the row that caused it.
 * @param full      true when the project is already at its audio-track limit.
 *                  The cards stay readable and previewable; only ＋ Add goes.
 */
export default function SoundLibrary({ onAdd, full = false }) {
  const [status, setStatus] = useState(null);
  const [query, setQuery] = useState("");
  const [licence, setLicence] = useState("safe");
  const [sort, setSort] = useState("relevance");
  const [page, setPage] = useState(1);

  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [addingId, setAddingId] = useState("");
  const [playingId, setPlayingId] = useState("");

  // ⚠ ONE ELEMENT, HELD IN A REF RATHER THAN RENDERED. It is never seen, it must
  // survive a re-render without restarting, and pressing ▶ on another row has to
  // be able to stop it — three things a per-card <audio> gets wrong.
  const audioRef = useRef(null);
  // Which request is the current one. A slow search for "rain" answering after a
  // fast one for "rains" would otherwise overwrite the newer list.
  const runRef = useRef(0);

  useEffect(() => {
    let alive = true;
    api
      .soundStatus()
      .then((s) => alive && setStatus(s))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, []);

  // Stop the preview when the tab goes away. Without this, switching to Media
  // leaves music playing from a pane that is no longer on screen.
  useEffect(
    () => () => {
      audioRef.current?.pause();
      audioRef.current = null;
    },
    []
  );

  // THE SEARCH. Debounced, and it re-runs on every control — the licence and the
  // sort are part of the query, not a filter over a list we already have, so a
  // client-side re-sort would show page 1 of the wrong thing.
  useEffect(() => {
    if (!status?.configured) return undefined;
    const mine = ++runRef.current;
    setLoading(true);
    const timer = setTimeout(() => {
      api
        .searchSounds({ q: query, licence, sort, page })
        .then((data) => {
          if (runRef.current !== mine) return;
          setResult(data);
          setError("");
        })
        .catch((e) => {
          if (runRef.current !== mine) return;
          setResult(null);
          setError(e.message);
        })
        .finally(() => {
          if (runRef.current === mine) setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [status?.configured, query, licence, sort, page]);

  // Changing WHAT is searched restarts at page 1; changing the page does not.
  // Kept as its own effect so the three setters do not each have to remember.
  useEffect(() => {
    setPage(1);
  }, [query, licence, sort]);

  function togglePlay(item) {
    const el = audioRef.current;
    if (playingId === item.id && el) {
      el.pause();
      setPlayingId("");
      return;
    }
    if (el) el.pause();
    if (!item.preview_url) {
      setError("Freesound has no preview for that sound.");
      return;
    }
    const next = new Audio(item.preview_url);
    // Loud sound effects are the norm on Freesound and this is a preview, not a
    // mix — half volume is kind to whoever is wearing headphones.
    next.volume = 0.5;
    next.onended = () => setPlayingId("");
    next.onerror = () => {
      setPlayingId("");
      setError("That preview would not play. Open it on Freesound to check.");
    };
    audioRef.current = next;
    next.play().catch(() => setPlayingId(""));
    setPlayingId(item.id);
  }

  async function add(item) {
    setAddingId(item.id);
    setError("");
    try {
      await onAdd?.(item);
      // The sound is in the project now and the editor will play it on the
      // timeline; a preview still running underneath it is two copies at once.
      audioRef.current?.pause();
      setPlayingId("");
    } catch (e) {
      setError(e.message);
    } finally {
      setAddingId("");
    }
  }

  // --- The three states that are not a list ---------------------------------
  if (status && !status.configured) {
    return (
      <div className="snd-lib snd-off">
        <p className="an-note">
          The sound library is switched off. Put a Freesound API key in the
          server's <code>.env</code> as <code>FREESOUND_API_KEY</code> and
          restart the backend — the key is free and issued straight away at{" "}
          <a href="https://freesound.org/apiv2/apply/" target="_blank" rel="noreferrer noopener">
            freesound.org/apiv2/apply
          </a>
          .
        </p>
      </div>
    );
  }

  const items = result?.items || [];
  const hasNext = !!result?.has_next;
  const licenceHint = LICENCE_OPTIONS.find((o) => o.id === licence)?.hint || "";

  return (
    <div className="snd-lib">
      <div className="snd-controls">
        <input
          type="search"
          className="an-prop-input snd-q"
          placeholder="Search music and sound effects…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Search the sound library"
        />
        <select
          className="an-select snd-sel"
          value={licence}
          onChange={(e) => setLicence(e.target.value)}
          aria-label="Which licences to search"
          title={licenceHint}
        >
          {LICENCE_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
        <select
          className="an-select snd-sel"
          value={sort}
          onChange={(e) => setSort(e.target.value)}
          aria-label="How to order the results"
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      {/* ⚠ THE LICENCE HINT IS STANDING PROSE HERE, not behind an ⓘ, and it is
          the one place in this editor where that is right: it changes with the
          picker above it, and what a user owes the author of a file they are
          about to put in a paid advert is not something to make them go
          looking for. */}
      <p className="an-note snd-hint">{licenceHint}</p>

      {!query && !loading && !items.length ? (
        <p className="snd-seeds">
          {SUGGESTIONS.map((s) => (
            <button key={s} type="button" className="snd-seed" onClick={() => setQuery(s)}>
              {s}
            </button>
          ))}
        </p>
      ) : null}

      {error ? <p className="an-note snd-err">{error}</p> : null}

      {loading && !items.length ? <p className="tiny muted snd-state">Searching…</p> : null}
      {!loading && !error && query && !items.length ? (
        <p className="tiny muted snd-state">
          Nothing under that licence. Try “CC0 + CC BY”, or a plainer word.
        </p>
      ) : null}

      <ul className={`snd-list ${loading ? "busy" : ""}`}>
        {items.map((item) => (
          <SoundCard
            key={item.id}
            item={item}
            playing={playingId === item.id}
            busy={addingId === item.id}
            disabled={full}
            onPlay={togglePlay}
            onAdd={add}
          />
        ))}
      </ul>

      {items.length ? (
        <div className="snd-pager">
          <button
            type="button"
            className="an-tool"
            disabled={page <= 1 || loading}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            ‹ Back
          </button>
          <span className="tiny muted">
            {/* `total` counts the SEARCH, not this page — results whose licence
                we do not recognise are dropped after Freesound counted them, so
                these two numbers can legitimately disagree. */}
            Page {page} · {result.total.toLocaleString()} sounds
          </span>
          <button
            type="button"
            className="an-tool"
            disabled={!hasNext || loading}
            onClick={() => setPage((p) => p + 1)}
          >
            More ›
          </button>
        </div>
      ) : null}

      {status?.notice ? <p className="an-note snd-tos">{status.notice}</p> : null}
    </div>
  );
}
