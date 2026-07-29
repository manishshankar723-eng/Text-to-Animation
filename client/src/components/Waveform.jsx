// Waveform.jsx — draws the animatic's audio track under the timeline.
//
// Seeing where the beats and the dialogue actually fall is the whole point of
// laying audio under a storyboard: it's what lets you drag a frame edge onto a
// beat instead of guessing. Drawn from the file itself with WebAudio — no
// library, and nothing is sent to the server.
import { useEffect, useRef, useState } from "react";

// Peaks are computed ONCE at this resolution and re-bucketed for whatever width
// the canvas happens to be, so zooming redraws instantly instead of decoding a
// multi-megabyte MP3 again.
const PEAK_BUCKETS = 4000;

async function computePeaks(url) {
  const res = await fetch(url);
  const bytes = await res.arrayBuffer();
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return null;
  const ctx = new Ctx();
  try {
    const buffer = await ctx.decodeAudioData(bytes);
    // Mixing to mono is enough for a shape, and halves the work on stereo files.
    const channels = [];
    for (let c = 0; c < buffer.numberOfChannels; c++) channels.push(buffer.getChannelData(c));

    const peaks = new Float32Array(PEAK_BUCKETS);
    const per = Math.max(1, Math.floor(buffer.length / PEAK_BUCKETS));
    for (let i = 0; i < PEAK_BUCKETS; i++) {
      const start = i * per;
      let peak = 0;
      for (let j = 0; j < per; j += 4) {
        // Step by 4: at this bucket size the extra samples don't change the
        // drawn shape, and it keeps a 10-minute file responsive.
        for (const data of channels) {
          const v = Math.abs(data[start + j] || 0);
          if (v > peak) peak = v;
        }
      }
      peaks[i] = peak;
    }
    return { peaks, durationMs: buffer.duration * 1000 };
  } finally {
    // Chrome caps how many AudioContexts a page may hold; a decode-only context
    // that is never closed will eventually stop new ones from being created.
    ctx.close?.();
  }
}

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
    computePeaks(audioUrl)
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
      const bucket = Math.min(PEAK_BUCKETS - 1, Math.floor((audioMs / durationMs) * PEAK_BUCKETS));
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
