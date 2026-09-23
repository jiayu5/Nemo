import { formatDuration } from "../api";
import type { ProviderSettings, Run, RunEvent, Trace } from "../types";
import { ProviderSettingsPanel } from "./ProviderSettingsPanel";

export type InspectorView = "activity" | "connections";

interface Props {
  view: InspectorView;
  onViewChange: (view: InspectorView) => void;
  runs: Run[];
  trace: Trace | null;
  events: RunEvent[];
  settings: ProviderSettings | null;
  onSelectRun: (run: Run) => void;
  onSettingsSaved: () => Promise<ProviderSettings>;
}

export function InspectorPanel({
  view, onViewChange, runs, trace, events, settings, onSelectRun, onSettingsSaved,
}: Props) {
  return (
    <aside className={`trace-panel panel ${view}-view`}>
      <div className="inspector-tabs" role="tablist" aria-label="Inspector view">
        <button
          type="button"
          role="tab"
          aria-selected={view === "activity"}
          onClick={() => onViewChange("activity")}
        >Activity</button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "connections"}
          onClick={() => onViewChange("connections")}
        >Connections</button>
      </div>
      {view === "connections" ? (
        <ProviderSettingsPanel settings={settings} onSaved={onSettingsSaved} />
      ) : (
        <>
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
        </>
      )}
    </aside>
  );
}
