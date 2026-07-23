// Inline progress state shown INSIDE the form card while the (single-call)
// script breakdown runs — a gold conic progress ring with a rotating step label.
// Distinct from a checklist; keeps the first page's two-column layout intact.
import { useEffect, useState } from "react";

const STEPS = [
  "Reading your story…",
  "Aligning it with the genre…",
  "Identifying the characters…",
  "Creating the scene breakdown…",
];

export default function BreakdownProgress() {
  const [pct, setPct] = useState(5);
  const [stepIdx, setStepIdx] = useState(0);

  useEffect(() => {
    // Ease toward ~95% (real completion unmounts us → Review).
    const p = setInterval(() => {
      setPct((v) => (v < 95 ? v + Math.max(1, Math.round((97 - v) * 0.07)) : v));
    }, 180);
    const s = setInterval(() => {
      setStepIdx((i) => (i + 1) % STEPS.length);
    }, 1400);
    return () => {
      clearInterval(p);
      clearInterval(s);
    };
  }, []);

  return (
    <div className="card bp-inline">
      <div className="bp-ring2" style={{ "--pct": `${pct}%` }}>
        <div className="bp-ring2-inner">
          <span className="bp-ring2-pct">{pct}%</span>
          <span className="bp-ring2-label">building</span>
        </div>
      </div>

      <h3 className="bp-inline-title">Generating your scene breakdown</h3>
      <p className="bp-inline-step" key={stepIdx}>
        {STEPS[stepIdx]}
      </p>

      <div className="bp-dots">
        {STEPS.map((_, i) => (
          <span key={i} className={`bp-dot2 ${i === stepIdx ? "on" : ""}`} />
        ))}
      </div>
    </div>
  );
}
