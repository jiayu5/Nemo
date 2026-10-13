import { formatDuration } from "../api";
import type { Run, RunEvent, Trace } from "../types";

interface Props {
  runs: Run[];
  trace: Trace | null;
  events: RunEvent[];
  onSelectRun: (run: Run) => void;
  onSelectSubagent?: (id: string) => void;
}

export function InspectorPanel({
  runs, trace, events, onSelectRun, onSelectSubagent,
}: Props) {
  const children = new Map<string, { status: string; step: number; round: number }>();
  for (const event of events) {
    if (event.type !== "subagent.event" || typeof event.payload.child_run_id !== "string") continue;
    const id = event.payload.child_run_id;
    const child = children.get(id) ?? { status: "queued", step: 0, round: 1 };
    child.step = Number(event.payload.child_step ?? child.step);
    const payload = event.payload.child_payload as Record<string, unknown> | undefined;
    child.round = Number(payload?.round ?? child.round);
    const type = String(event.payload.child_type ?? "");
    if (type === "subagent.resumed") child.status = "queued";
    if (type === "run.started") child.status = "running";
    else if (type.startsWith("run.") && type !== "run.started") child.status = type.slice(4);
    else if (type === "subagent.finished") {
      child.status = String(payload?.status ?? child.status);
    }
    children.set(id, child);
  }
  const visibleEvents = events.filter(event => event.type !== "subagent.event" ||
    !["model.delta", "model.reasoning_delta"].includes(String(event.payload.child_type)));
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
    {!!children.size && <section className="subagent-summary" aria-label="Subagents">
      <strong>Subagents</strong>
      {[...children].map(([id, child]) => <button type="button" className="subagent-status" key={id} aria-label={`View subagent ${id}`}
        onClick={() => onSelectSubagent?.(id)}>
        <span>Subagent {id.slice(0, 7)}</span><span className={`status-dot ${child.status}`} />
        <small>{child.status} · step {child.step}{child.round > 1 ? ` · round ${child.round}` : ""}</small>
      </button>)}
    </section>}
    <div className="event-list">
      {visibleEvents.map((event) => (
        <div className="event-row" key={event.seq}>
          <b>{event.seq}</b>
          {event.type === "subagent.event" ? (
            <span>Subagent {String(event.payload.child_run_id ?? "").slice(0, 7)} · {String(event.payload.child_type ?? "")} · step {String(event.payload.child_step ?? 0)}</span>
          ) : event.type === "context.compacted" ? (
            <details>
              <summary>Context compressed through message {String(event.payload.covered_messages)}</summary>
              <p>{String(event.payload.summary ?? "")}</p>
            </details>
          ) : event.type === "context.compression_completed" ? (
            <span>Compression model call · prompt {String(event.payload.prompt_tokens ?? "unknown")} · reply {String(event.payload.completion_tokens ?? "unknown")} tokens</span>
          ) : event.type === "mcp.connection_skipped" ? (
            <span>
              {event.payload.server_id ? `MCP ${String(event.payload.server_id)}` : "MCP tools"} not loaded · {String(event.payload.reason ?? "")}
            </span>
          ) : <span>{event.type}</span>}
          <small>step {event.step}</small>
        </div>
      ))}
      {!visibleEvents.length && <p className="empty-note">Run events will appear here.</p>}
    </div>
    </aside>
  );
}
