// useAudioAnalysis.js — one decoded analysis per audio track, for whoever asks.
//
// `beats.js` owns the decode and caches it by url; this is the React end of it,
// so the timeline can draw beat markers and the transport can duck the preview
// without either of them starting a decode of its own. The waveform reads the
// same cache directly, so a track is decoded exactly once however many things
// are looking at it.
//
// Deliberately NOT part of `useTimelineTransport`: the transport owns the clock
// and nothing else, and an analysis that arrives two seconds after the page does
// must not be able to restart it.

import { useEffect, useState } from "react";
import { analyseAudio } from "./beats.js";

/**
 * @param audioUrls  upload_id → blob url (the editor's map)
 * @returns          upload_id → { peaks, envelope, hopMs, durationMs, beats }
 */
export default function useAudioAnalysis(audioUrls) {
  const [analyses, setAnalyses] = useState({});

  useEffect(() => {
    let alive = true;
    const ids = Object.keys(audioUrls || {});

    // Drop the tracks that have gone before adding the ones that arrived, so a
    // removed track's markers leave the timeline on the same render.
    setAnalyses((was) => {
      const kept = {};
      for (const id of ids) if (was[id]) kept[id] = was[id];
      return Object.keys(kept).length === Object.keys(was).length ? was : kept;
    });

    for (const id of ids) {
      analyseAudio(audioUrls[id])
        .then((result) => {
          if (!alive || !result) return;
          setAnalyses((was) => (was[id] === result ? was : { ...was, [id]: result }));
        })
        .catch(() => {
          /* no waveform, no markers — the track still plays and still exports */
        });
    }

    return () => {
      alive = false;
    };
  }, [audioUrls]);

  return analyses;
}
