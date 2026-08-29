// dashboard_feed.js — WHAT THIS ACCOUNT HAS MADE, one group per workflow, read
// synchronously out of the session cache. Plus the two hooks that go with it:
// the subscribe/refresh pair, and the cover fetcher.
//
// ⚠ THIS EXISTS BECAUSE THERE ARE TWO DASHBOARDS NOW. `Home` prints two items
// per workflow as a list; `Explore` prints everything it has as one gallery.
// Both need the same answer to "what has this account made, in which workflow,
// with which cover picture" — and Home's own header already said what a second
// copy of that answer would cost:
//
//     "ORDER MATCHES THE SIDEBAR — when a workflow is added, renamed or moved
//      in Sidebar.jsx, it has to be added, renamed or moved here too, or Recent
//      work quietly stops showing it (which is exactly how Image to Video went
//      missing)."
//
// That warning was written about ONE list. Two lists is the same trap twice, so
// the list lives here and both screens read it. ⚠ THE ORDER STILL HAS TO MATCH
// `WORKFLOWS` IN `Sidebar.jsx` — keep the two in step.
//
// ⚠ NOTHING IN HERE FETCHES A LIST. Every group is read out of `session_cache`,
// which was filled at sign-in, before React knew anything had happened. See the
// header of that file for what that replaced and why.
import { useEffect, useReducer, useRef, useState } from "react";
import * as api from "./api.js";
import * as cache from "./session_cache.js";
import { THUMB_EDGE } from "./components/LibraryList.jsx";

// `/jobs` answers with a bare array; one older shape wrapped it. Normalised in
// one place rather than at each use.
export function asList(value) {
  if (Array.isArray(value)) return value;
  if (value && Array.isArray(value.jobs)) return value.jobs;
  return [];
}

/**
 * The number to print in "View all (N)", or `null` for "we can't say".
 *
 * ⚠ THE DASHBOARD ONLY FETCHES A PAGE (`DASH_LIMIT`), so the length of the list
 * it holds is NOT the size of the library — and quietly printing it would turn
 * "View all (40)" into "View all (8)" for the busiest accounts, which is the
 * sort of wrong number nobody reports and everybody half-notices.
 *
 * Two ways to be sure, and if neither applies we print no number at all:
 *   - the page came back SHORT of the limit, so the page is everything;
 *   - the login hint counts these job kinds exactly (see TokenResponse.counts).
 *
 * `kinds` is empty for the two storyboard groups on purpose: both are made of
 * `storyboard` records and the hint cannot tell an original from a copy, so a
 * full page of either is honestly unknown.
 */
export function totalFor(list, kinds) {
  if (list.length < cache.DASH_LIMIT) return list.length;
  const counts = cache.hint();
  if (counts && kinds.length) {
    const n = kinds.reduce((sum, k) => sum + (Number(counts[k]) || 0), 0);
    if (n >= list.length) return n;
  }
  return null;
}

/**
 * Subscribe to the session cache and re-render whenever it changes.
 *
 * ⚠ IT READS SYNCHRONOUSLY AND ONLY THEN ASKS FOR A REFRESH. That order is what
 * removes the loader: by the time this runs the prefetch started at sign-in has
 * usually landed, so the first paint is real content and the refresh is a
 * silent top-up behind it. Nothing here can start a duplicate request — the
 * cache joins whatever is already in flight.
 *
 * ⚠ AND IT IS `refresh`, NOT `ensure` — the lists are re-read on EVERY mount,
 * staleness window ignored. This app has no router: leaving a screen unmounts
 * it and coming back mounts it again, and the commonest reason to come back is
 * that you just made something. A cache that answered "still fresh, I read this
 * forty seconds ago" would show a customer a dashboard with their new project
 * missing from it — which is a worse bug than the slowness this all started as.
 *
 * It costs what it should: five small requests that nobody waits for, because
 * what is already cached stays on screen throughout.
 */
export function useDashboard() {
  const [, bump] = useReducer((n) => n + 1, 0);
  useEffect(() => cache.subscribe(bump), []);
  useEffect(() => {
    // `me` / `entitlements` are deliberately not in here — the shell owns those
    // and they do not change while you are signed in.
    cache.refresh(cache.LIST_KEYS);
  }, []);
}

/**
 * Fetch each item's cover picture once, as an authed object URL.
 *
 * ⚠ THIS IS NOT A LIST FETCH. Those all happen at sign-in (see the module
 * header). These are pictures for content that is already on screen: nothing
 * waits for them, a failure leaves the workflow's own icon in place, and they
 * are asked for at `THUMB_EDGE` rather than full size.
 *
 * ⚠ `asked` IS WHAT MAKES IT ONCE. `items` is a fresh array on every render, so
 * the effect re-runs constantly; the set is what stops a re-render becoming a
 * re-fetch.
 *
 * ⚠ AND THE REVOKE IS TIED TO UNMOUNT, NOT TO THE EFFECT RE-RUNNING. Revoking
 * on every cleanup would throw away the URL of a fetch that started one render
 * ago — and because `asked` already holds its key, nothing would ever ask for
 * that picture again and the row would keep the placeholder for ever.
 *
 * ⚠ AND THE UNMOUNT PATH HAS TO UNDO **ALL THREE** PIECES OF STATE, BECAUSE OF
 * `React.StrictMode` (main.jsx). In development it mounts, tears down, and
 * mounts again — with the component's state kept. The first version of this
 * hook only ever set its `live` flag to FALSE on the way out and never back to
 * true, so on the second, real mount:
 *
 *   · `live` was still false, so every picture that arrived was revoked and
 *     thrown away, and
 *   · `asked` still held every key, so nothing was ever requested again.
 *
 * Result: not one cover ever appeared on the dashboard — reported as "see not
 * view image". So the setup RE-ARMS `live`, and the cleanup forgets what was
 * asked for AND clears `urls`: state survives a StrictMode remount, so leaving
 * the map in place would leave `<img src>` pointing at blobs that were just
 * revoked, which is a broken picture rather than a missing one.
 *
 * The cost in dev is that each picture is fetched twice. That is what
 * StrictMode is FOR — it is the same double-fetch it forces on every other
 * effect in the app, and it does not happen in a production build.
 */
export function useCovers(items) {
  const [urls, setUrls] = useState({});
  const made = useRef([]);
  const asked = useRef(new Set());
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      made.current.forEach((u) => URL.revokeObjectURL(u));
      made.current = [];
      asked.current = new Set();
      setUrls({});
    };
  }, []);

  useEffect(() => {
    for (const it of items) {
      if (!it.loadCover || asked.current.has(it.key)) continue;
      asked.current.add(it.key);
      it.loadCover()
        .then((url) => {
          if (!live.current) {
            URL.revokeObjectURL(url);
            return;
          }
          made.current.push(url);
          setUrls((m) => ({ ...m, [it.key]: url }));
        })
        .catch(() => {}); // a missing cover just leaves the workflow's icon
    }
  }, [items]);

  return urls;
}

/**
 * Every workflow's group, in sidebar order.
 *
 * @param {object} opts
 * @param {(jobId: string) => void} [opts.onOpenJob] — Text to Turnaround Image
 *   is the only workflow that can open a single job; without this, its items
 *   fall back to opening the workflow, exactly like every other group's do.
 * @param {(message: string) => void} [opts.onError] — where a failed asset ZIP
 *   goes. A screen that offers no ZIP button never calls it.
 *
 * Each group is `{ id, icon, label, total, items[] }`, and each item is
 * `{ key, title, status?, meta?, aspect?, size?, date?, loadCover?, onOpen?, zip? }`.
 */
export function buildGroups({ onOpenJob, onError } = {}) {
  const jobs = asList(cache.read("jobs"));
  const boards = asList(cache.read("boards"));
  // Image to Animatic Image's own copies — a different set from `boards`.
  const copiedBoards = asList(cache.read("copiedBoards"));
  const animatics = asList(cache.read("animatics"));
  const videos = asList(cache.read("videos"));
  const plans = asList(cache.read("plans"));

  return [
    {
      id: "plan-and-script",
      icon: "🗓️",
      label: "Plan & Script",
      total: totalFor(plans, ["plan"]),
      items: plans.map((p) => ({
        key: p.job_id,
        title: p.title || "Untitled plan",
        meta: p.item_count > 0 ? `${p.item_count} uploads` : "no plan yet",
        date: p.updated_at || p.created_at
      }))
    },
    {
      id: "text-to-image",
      icon: "🖼️",
      label: "Text to Turnaround Image",
      // Both character kinds land in this one list — see CHARACTER_JOB_KINDS.
      total: totalFor(jobs, ["generate", "meshy"]),
      items: jobs.map((j) => ({
        key: j.job_id,
        title: j.character_name || "Untitled",
        status: j.status,
        date: j.created_at,
        // Only this workflow can open a job detail and serve an asset ZIP.
        onOpen: onOpenJob ? () => onOpenJob(j.job_id) : null,
        zip:
          j.status === "succeeded"
            ? () =>
                api
                  .downloadZip(
                    j.job_id,
                    `${j.character_name}_assets.zip`,
                    j.result?.zip
                  )
                  .catch((e) => onError?.(e.message))
            : null
      }))
    },
    {
      id: "script-to-storyboard",
      icon: "📝",
      label: "Script to Storyboard",
      // No kinds: originals and copies are both `storyboard` records, so the
      // hint cannot separate them. See totalFor.
      total: totalFor(boards, []),
      items: boards.map((b) => ({
        key: b.job_id,
        title: b.title || "Storyboard",
        status: b.status,
        meta: b.panel_count ? `${b.panel_count} panels` : "",
        aspect: b.aspect_ratio,
        size: b.size_bytes,
        loadCover:
          b.cover_index === null || b.cover_index === undefined
            ? null
            : () =>
                api.fetchStoryboardPanel(
                  b.job_id,
                  b.cover_index,
                  b.cover_url,
                  THUMB_EDGE
                ),
        date: b.created_at
      }))
    },
    {
      // Its OWN boards — independent copies made by its "From a Storyboard"
      // tile, not the originals. Drawing in a copy must never change the
      // storyboard it came from, so the two sets are kept apart everywhere.
      id: "create-animatic-image",
      icon: "🖼️",
      label: "Image to Animatic Image",
      total: totalFor(copiedBoards, []),
      items: copiedBoards.map((b) => ({
        key: b.job_id,
        title: b.title || "Storyboard",
        status: b.status,
        meta: b.panel_count ? `${b.panel_count} panels` : "",
        aspect: b.aspect_ratio,
        size: b.size_bytes,
        loadCover:
          b.cover_index === null || b.cover_index === undefined
            ? null
            : () =>
                api.fetchStoryboardPanel(
                  b.job_id,
                  b.cover_index,
                  b.cover_url,
                  THUMB_EDGE
                ),
        date: b.updated_at || b.created_at
      }))
    },
    {
      id: "animatics-to-video",
      icon: "🎞️",
      label: "Image to AI Video",
      total: totalFor(videos, ["final_video"]),
      items: videos.map((v) => ({
        key: v.job_id,
        title: v.title || "Final video",
        status: v.status,
        // How much is DONE, not just how much is in it — this is the only
        // workflow where the remainder costs money to finish.
        meta: v.shot_count ? `${v.rendered_count}/${v.shot_count} rendered` : "",
        aspect: v.aspect_ratio,
        size: v.size_bytes,
        loadCover: v.cover_url
          ? () => api.fetchFinalVideoMedia(v.cover_url, THUMB_EDGE)
          : null,
        date: v.updated_at || v.created_at
      }))
    },
    {
      id: "storyboard-to-animatics",
      icon: "🎬",
      label: "Video Editor",
      total: totalFor(animatics, ["animatic"]),
      items: animatics.map((a) => ({
        key: a.job_id,
        title: a.title || "Project",
        status: a.status,
        // Same shape of hint as the others: how much is in it.
        meta: a.frame_count ? `${a.frame_count} frames` : "",
        aspect: a.aspect_ratio,
        size: a.size_bytes,
        loadCover: a.cover_url
          ? () => api.fetchAnimaticMedia(a.cover_url, THUMB_EDGE)
          : null,
        date: a.updated_at || a.created_at
      }))
    }
  ];
}

// The class a status badge is drawn with. Shared so the dashboard's list and
// the gallery cannot drift into two different colours for one word.
export function statusClass(status) {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "fail";
  if (status === "running") return "running";
  return "queued";
}

// "28 Aug". One format for both dashboards.
export function formatDate(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
