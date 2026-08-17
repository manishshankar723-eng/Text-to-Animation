// Waveform.jsx — draws the animatic's audio track under the timeline.
//
// Seeing where the beats and the dialogue actually fall is the whole point of
// laying audio under a storyboard: it's what lets you drag a frame edge onto a
// beat instead of guessing. Drawn from the file itself with WebAudio — no
// library, and nothing is sent to the server.
import { useEffect, useRef, useState } from "react";
import { analyseAudio } from "../animatic/beats.js";

// ⚠ THE DECODE MOVED to `animatic/beats.js` and is cached there by url. The
// peaks are computed exactly as they were — same buckets, same stepping — but
// the beat markers and the duck preview want the same samples, so decoding a
// multi-megabyte MP3 once per thing that looks at it was both slow and three
// chances to disagree about how long the file is.

export default function Waveform({
  audioUrl,
  width,
  height = 48,
  // Video time shown across the full width — the waveform has to line up with
  // the frame bars above it, so it is drawn in VIDEO time, not audio time.
  totalMs,
  offsetMs = 0,
  className = "",
}) {
  const canvasRef = useRef(null);
  const [data, setData] = useState(null);
  const [state, setState] = useState("idle"); // idle | loading | ready | error

  useEffect(() => {
    if (!audioUrl) {
      setData(null);
      setState("idle");
      return;
    }
    let alive = true;
    setState("loading");
    analyseAudio(audioUrl)
      .then((result) => {
        if (!alive) return;
        setData(result);
        setState(result ? "ready" : "error");
      })
      .catch(() => alive && setState("error"));
    return () => {
      alive = false;
    };
  }, [audioUrl]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !data || !width) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.floor(height * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const styles = getComputedStyle(canvas);
    ctx.fillStyle = styles.getPropertyValue("--wave-ink").trim() || "#e5c158";

    const { peaks, durationMs } = data;
    const mid = height / 2;
    const span = Math.max(1, totalMs || durationMs);

    for (let x = 0; x < width; x++) {
      // x is a position in VIDEO time; offsetMs shifts it into the audio file.
      const audioMs = (x / width) * span + offsetMs;
      if (audioMs < 0 || audioMs > durationMs) continue;
      // Re-bucketed from the analysis's own resolution, whatever that is — the
      // canvas is redrawn on every zoom step and must never assume a number
      // that lives in another file.
      const bucket = Math.min(peaks.length - 1, Math.floor((audioMs / durationMs) * peaks.length));
      const amp = Math.max(0.02, peaks[bucket]) * (height / 2) * 0.94;
      ctx.fillRect(x, mid - amp, 1, amp * 2);
    }
  }, [data, width, height, totalMs, offsetMs]);

  if (state === "loading") {
    return <div className={`wave-msg ${className}`}>Reading the audio…</div>;
  }
  if (state === "error") {
    return (
      <div className={`wave-msg ${className}`}>
        Couldn't draw this waveform — the audio still plays and still exports.
      </div>
    );
  }
  if (state !== "ready") return null;

  return <canvas ref={canvasRef} className={`wave-canvas ${className}`} style={{ height }} />;
}
