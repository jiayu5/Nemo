import { formatDuration } from "../api";
import type { Run, RunEvent, Trace } from "../types";

interface Props {
  runs: Run[];
  trace: Trace | null;
  events: RunEvent[];
  onSelectRun: (run: Run) => void;
}

export function InspectorPanel({
  runs, trace, events, onSelectRun,
}: Props) {
  return (
    <aside className="trace-panel panel activity-view" aria-label="Run activity">
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
          <b>{event.seq}</b>
          {event.type === "context.compacted" ? (
            <details>
              <summary>Context compressed through message {String(event.payload.covered_messages)}</summary>
              <p>{String(event.payload.summary ?? "")}</p>
            </details>
          ) : event.type === "context.compression_completed" ? (
            <span>Compression model call · prompt {String(event.payload.prompt_tokens ?? "unknown")} · reply {String(event.payload.completion_tokens ?? "unknown")} tokens</span>
          ) : <span>{event.type}</span>}
          <small>step {event.step}</small>
        </div>
      ))}
      {!events.length && <p className="empty-note">Run events will appear here.</p>}
    </div>
    </aside>
  );
}
