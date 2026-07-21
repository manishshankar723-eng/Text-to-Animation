// Placeholder screen for workflows that are on the roadmap but not built yet.
export default function WorkflowSoon({ icon, title, description, steps }) {
  return (
    <div className="soon-wrap">
      <div className="card soon-card">
        <span className="soon-icon">{icon}</span>
        <span className="soon-tag">Coming soon</span>
        <h1>{title}</h1>
        <p className="muted soon-desc">{description}</p>

        {steps?.length > 0 && (
          <div className="soon-flow">
            {steps.map((s, i) => (
              <div className="soon-step" key={i}>
                <span className="soon-step-dot">{i + 1}</span>
                <span>{s}</span>
              </div>
            ))}
          </div>
        )}

        <p className="tiny muted soon-foot">
          This workflow is part of the full script-to-video pipeline in progress.
        </p>
      </div>
    </div>
  );
}
