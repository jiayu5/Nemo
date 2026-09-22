import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, formatDuration, subscribeToRun } from "./api";
import type {
  ApprovalMode,
  ApprovalRequest,
  Message,
  ModelOption,
  Provider,
  Run,
  RunEvent,
  Session,
  Trace,
} from "./types";

const TERMINAL = new Set([
  "run.completed",
  "run.failed",
  "run.cancelled",
  "run.interrupted",
  "run.limit_reached",
]);

export default function App() {
  const [serverOnline, setServerOnline] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [approval, setApproval] = useState<ApprovalRequest | null>(null);
  const [workspace, setWorkspace] = useState(".");
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<EventSource | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  const loadSession = useCallback(async (session: Session) => {
    setSelected(session);
    setError(null);
    const [history, sessionRuns] = await Promise.all([
      api.messages(session.session_id),
      api.runs(session.session_id),
    ]);
    setMessages(history);
    setRuns(sessionRuns);
    const newest = sessionRuns[0];
    if (newest) {
      const newestTrace = await api.trace(newest.run_id);
      setTrace(newestTrace);
      setEvents(newestTrace.events);
    } else {
      setTrace(null);
      setEvents([]);
    }
  }, []);

  const refreshSessions = useCallback(async () => {
    const items = await api.sessions();
    setSessions(items);
    return items;
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.health(), api.sessions(), api.models(), api.providers()])
      .then(async ([, sessionItems, modelItems, providerItems]) => {
        if (cancelled) return;
        setServerOnline(true);
        setSessions(sessionItems);
        setModels(modelItems);
        setProviders(providerItems);
        if (sessionItems[0]) await loadSession(sessionItems[0]);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "Server unavailable");
      });
    return () => {
      cancelled = true;
      streamRef.current?.close();
    };
  }, [loadSession]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages]);

  async function createSession(event: FormEvent) {
    event.preventDefault();
    try {
      const model = models.find((item) => item.is_default)?.selection ?? null;
      const session = await api.createSession(workspace, model, "ask");
      await refreshSessions();
      await loadSession(session);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create session");
    }
  }

  async function finishRun(runId: string) {
    streamRef.current?.close();
    streamRef.current = null;
    setActiveRun(null);
    setApproval(null);
    if (!selected) return;
    const [history, sessionRuns, runTrace, sessionItems] = await Promise.all([
      api.messages(selected.session_id),
      api.runs(selected.session_id),
      api.trace(runId),
      api.sessions(),
    ]);
    setMessages(history);
    setRuns(sessionRuns);
    setTrace(runTrace);
    setEvents(runTrace.events);
    setSessions(sessionItems);
    setSelected(sessionItems.find((item) => item.session_id === selected.session_id) ?? selected);
  }

  async function submitPrompt(event: FormEvent) {
    event.preventDefault();
    if (!selected || !prompt.trim() || activeRun) return;
    const text = prompt.trim();
    setPrompt("");
    setError(null);
    setMessages((current) => [
      ...current,
      { role: "user", content: text, tool_calls: [], tool_result: null },
    ]);
    try {
      const run = await api.startRun(selected.session_id, text);
      setActiveRun(run);
      setEvents([]);
      setTrace(null);
      streamRef.current = subscribeToRun(
        run.run_id,
        (runEvent) => {
          setEvents((current) => [...current, runEvent]);
          if (runEvent.type === "approval.requested") {
            setApproval(runEvent.payload as unknown as ApprovalRequest);
          }
          if (TERMINAL.has(runEvent.type)) {
            void finishRun(run.run_id).catch((reason: unknown) =>
              setError(reason instanceof Error ? reason.message : "Could not refresh completed run"),
            );
          }
        },
        () => setError("Event stream disconnected; the Run may still be active."),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start run");
      setActiveRun(null);
    }
  }

  function handlePromptKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  async function answerApproval(outcome: "allow_once" | "allow_session" | "deny") {
    if (!activeRun || !approval) return;
    try {
      await api.approve(activeRun.run_id, approval.request_id, outcome);
      setApproval(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not answer approval");
    }
  }

  async function updateMode(mode: ApprovalMode) {
    if (!selected) return;
    try {
      const session = await api.updateMode(selected.session_id, mode);
      setSelected(session);
      await refreshSessions();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update approval mode");
    }
  }

  async function updateModel(model: string) {
    if (!selected) return;
    try {
      const session = await api.updateModel(selected.session_id, model);
      setSelected(session);
      await refreshSessions();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update model");
    }
  }

  async function selectRun(run: Run) {
    try {
      const value = await api.trace(run.run_id);
      setTrace(value);
      setEvents(value.events);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load run trace");
    }
  }

  async function cancelRun() {
    if (!activeRun) return;
    try {
      await api.cancelRun(activeRun.run_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not stop run");
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <div className="brand-mark">N</div>
          <div>
            <h1>Nemo</h1>
            <p>Local agent runtime</p>
          </div>
        </div>
        <div className="topbar-context">
          <span className="context-label">Current workspace</span>
          <span className="context-value">
            {selected?.workspace.split("/").filter(Boolean).at(-1) ?? "No session"}
          </span>
        </div>
        <span className={`server-pill ${serverOnline ? "online" : "offline"}`}>
          <i /> {serverOnline ? "Server online" : "Server offline"}
        </span>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <main className="workspace-grid">
        <aside className="sessions-panel panel">
          <div className="panel-heading">
            <div><span className="section-note">Local workspaces</span><strong>Recent sessions</strong></div>
            <b>{sessions.length}</b>
          </div>
          <form className="new-session" onSubmit={createSession}>
            <label htmlFor="workspace">Open a workspace</label>
            <input
              id="workspace"
              value={workspace}
              onChange={(event) => setWorkspace(event.target.value)}
              placeholder="/path/to/project"
            />
            <button type="submit" aria-label="New session"><span>＋</span> New session</button>
          </form>
          <div className="session-list">
            {sessions.map((session) => (
              <button
                className={selected?.session_id === session.session_id ? "selected" : ""}
                key={session.session_id}
                onClick={() =>
                  void loadSession(session).catch((reason: unknown) =>
                    setError(reason instanceof Error ? reason.message : "Could not load session"),
                  )
                }
              >
                <span>{session.workspace.split("/").filter(Boolean).at(-1) || "/"}</span>
                <small>{session.message_count} messages · {session.approval_mode}</small>
              </button>
            ))}
            {!sessions.length && <p className="empty-note">Create a session to begin.</p>}
          </div>
        </aside>

        <section className="conversation-panel panel">
          <div className="panel-heading conversation-title">
            <div>
              <span>{selected ? "New task" : "No session selected"}</span>
              <small>{selected?.workspace ?? "Choose a workspace on the left"}</small>
            </div>
            {activeRun && (
              <button className="danger-button" onClick={() => void cancelRun()}>
                Stop
              </button>
            )}
          </div>

          <div className="messages" aria-label="Conversation messages">
            {messages.map((message, index) => (
              <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
                <span className="role">{message.role}</span>
                {message.role === "tool" ? (
                  <pre>{JSON.stringify(message.tool_result, null, 2)}</pre>
                ) : (
                  <div className="message-content">
                    <Markdown remarkPlugins={[remarkGfm]}>
                      {message.content ||
                        (message.tool_calls.length
                          ? `Requested: ${message.tool_calls.map((call) => call.name).join(", ")}`
                          : "")}
                    </Markdown>
                  </div>
                )}
              </article>
            ))}
            {!messages.length && selected && (
              <div className="conversation-empty">
                <div className="empty-orb"><span>N</span></div>
                <p className="empty-kicker">Runtime ready</p>
                <h2>What should we work on?</h2>
                <p className="empty-copy">Ask Nemo to inspect code, make a change, or explain this workspace.</p>
                <div className="suggestion-row">
                  <button type="button" onClick={() => setPrompt("Summarize this project and its architecture")}>Summarize project</button>
                  <button type="button" onClick={() => setPrompt("Find the highest-priority improvement to make next")}>Suggest next step</button>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          {approval && (
            <section className="approval-card">
              <div><strong>Approval required</strong><span>{approval.tool_name}</span></div>
              <p>{approval.summary || approval.reason}</p>
              <div className="approval-actions">
                <button onClick={() => void answerApproval("allow_once")}>Allow once</button>
                <button onClick={() => void answerApproval("allow_session")}>Allow session</button>
                <button className="danger-button" onClick={() => void answerApproval("deny")}>Deny</button>
              </div>
            </section>
          )}

          <form className="composer" onSubmit={submitPrompt}>
            <div className="composer-surface">
              <textarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                onKeyDown={handlePromptKeyDown}
                placeholder={selected ? "Ask anything" : "Create a session first"}
                disabled={!selected || Boolean(activeRun)}
                rows={3}
              />
              <div className="composer-toolbar">
                <button className="composer-add" type="button" aria-label="Add context">＋</button>
                <label className="composer-control approval-control">
                  <span className="approval-icon" aria-hidden="true">!</span>
                  <select
                    aria-label="Approval"
                    value={selected?.approval_mode ?? "ask"}
                    disabled={!selected || Boolean(activeRun)}
                    onChange={(event) => void updateMode(event.target.value as ApprovalMode)}
                  >
                    <option value="ask">Ask</option>
                    <option value="auto">Auto</option>
                    <option value="full">Full</option>
                  </select>
                </label>
                <label className="composer-control model-control">
                  <select
                    aria-label="Model"
                    value={selected?.model ?? models.find((item) => item.is_default)?.selection ?? ""}
                    disabled={!selected || Boolean(activeRun)}
                    onChange={(event) => void updateModel(event.target.value)}
                  >
                    {models.map((model) => (
                      <option key={model.selection} value={model.selection}>
                        {model.selection} · {model.provider_id}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="composer-send"
                  type="submit"
                  aria-label="Run task"
                  disabled={!selected || !prompt.trim() || Boolean(activeRun)}
                >
                  {activeRun ? <span className="running-spinner" /> : "↑"}
                </button>
              </div>
            </div>
          </form>
        </section>

        <aside className="trace-panel panel">
          <div className="panel-heading inspector-heading">
            <div><span className="section-note">Execution details</span><strong>Run activity</strong></div>
            <span className="live-indicator">Live</span>
          </div>
          <div className="run-list">
            {runs.map((run) => (
              <button key={run.run_id} onClick={() => void selectRun(run)}>
                <span className={`status-dot ${run.status}`} />
                <span>Run {run.run_id.slice(0, 7)}</span>
                <small>{run.status}</small>
              </button>
            ))}
          </div>
          {trace && (
            <section className="trace-summary">
              <div className="metric-row">
                <span>Duration</span><strong>{formatDuration(trace.duration_ms)}</strong>
              </div>
              <div className="metric-row">
                <span>Model</span><strong>{trace.run.model_selection || "unknown"}</strong>
              </div>
              <div className="metric-row">
                <span>Steps</span><strong>{trace.steps.length}</strong>
              </div>
              <div className="metric-row">
                <span>Tools</span><strong>{trace.tool_calls.length}</strong>
              </div>
            </section>
          )}
          <div className="event-list">
            {events.map((event) => (
              <div className="event-row" key={event.seq}>
                <b>{event.seq}</b>
                <span>{event.type}</span>
                <small>step {event.step}</small>
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
      </main>

      <footer className="statusbar">
        <span className="workspace-status"><i /><b>Workspace</b> {selected?.workspace ?? "—"}</span>
      </footer>
    </div>
  );
}
