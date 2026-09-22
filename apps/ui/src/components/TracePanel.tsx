import { formatDuration } from "../api";
import type { Provider, Run, RunEvent, Trace } from "../types";

interface Props {
  runs: Run[];
  trace: Trace | null;
  events: RunEvent[];
  providers: Provider[];
  onSelectRun: (run: Run) => void;
}

export function TracePanel({ runs, trace, events, providers, onSelectRun }: Props) {
  return (
    <aside className="trace-panel panel">
      <div className="panel-heading inspector-heading">
        <div><span className="section-note">Execution details</span><strong>Run activity</strong></div>
        <span className="live-indicator">Live</span>
      </div>
      <div className="run-list">
        {runs.map((run) => (
          <button key={run.run_id} onClick={() => onSelectRun(run)}>
            <span className={`status-dot ${run.status}`} />
            <span>Run {run.run_id.slice(0, 7)}</span>
            <small>{run.status}</small>
          </button>
        ))}
      </div>
      {trace && (
        <section className="trace-summary">
          <div className="metric-row"><span>Duration</span><strong>{formatDuration(trace.duration_ms)}</strong></div>
          <div className="metric-row"><span>Model</span><strong>{trace.run.model_selection || "unknown"}</strong></div>
          <div className="metric-row"><span>Steps</span><strong>{trace.steps.length}</strong></div>
          <div className="metric-row"><span>Tools</span><strong>{trace.tool_calls.length}</strong></div>
        </section>
      )}
      <div className="event-list">
        {events.map((event) => (
          <div className="event-row" key={event.seq}>
            <b>{event.seq}</b><span>{event.type}</span><small>step {event.step}</small>
          </div>
        ))}
        {!events.length && <p className="empty-note">Run events will appear here.</p>}
      </div>
      <section className="providers-card">
        <div className="providers-title"><h3>Connections</h3><span>{providers.length}</span></div>
        {providers.map((provider) => (
          <div key={provider.provider_id}>
            <span>{provider.provider_id}</span>
            <small className={provider.secret_configured ? "configured" : "missing"}>
              {provider.secret_configured ? "configured" : "missing key"}
            </small>
          </div>
        ))}
      </section>
    </aside>
  );
}
