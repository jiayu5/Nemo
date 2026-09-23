import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, subscribeToRun } from "../api";
import { isTerminalRunEvent } from "../events";
import type {
  ApprovalMode,
  ApprovalRequest,
  Message,
  ModelOption,
  Provider,
  ProviderSettings,
  Run,
  RunEvent,
  Session,
  Trace,
} from "../types";

function message(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

export function useNemoWorkspace() {
  const [serverOnline, setServerOnline] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [providerSettings, setProviderSettings] = useState<ProviderSettings | null>(null);
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [approval, setApproval] = useState<ApprovalRequest | null>(null);
  const [workspace, setWorkspace] = useState(".");
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const streamRef = useRef<EventSource | null>(null);

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

  const refreshConfiguration = useCallback(async () => {
    const [settings, modelItems, providerItems] = await Promise.all([
      api.providerSettings(),
      api.models().catch(() => [] as ModelOption[]),
      api.providers().catch(() => [] as Provider[]),
    ]);
    setProviderSettings(settings);
    setModels(modelItems);
    setProviders(providerItems);
    return settings;
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.health(),
      api.sessions(),
      api.providerSettings(),
      api.models().catch(() => [] as ModelOption[]),
      api.providers().catch(() => [] as Provider[]),
    ])
      .then(async ([, sessionItems, settings, modelItems, providerItems]) => {
        if (cancelled) return;
        setServerOnline(true);
        setSessions(sessionItems);
        setModels(modelItems);
        setProviders(providerItems);
        setProviderSettings(settings);
        if (sessionItems[0]) await loadSession(sessionItems[0]);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(message(reason, "Server unavailable"));
      });
    return () => {
      cancelled = true;
      streamRef.current?.close();
    };
  }, [loadSession]);

  async function createSession(event: FormEvent) {
    event.preventDefault();
    try {
      const defaultOption = models.find((item) => item.is_default);
      const model = defaultOption?.model_name
        ?? models.find((item) => item.kind === "model")?.selection
        ?? null;
      const session = await api.createSession(workspace, model, "ask");
      await refreshSessions();
      await loadSession(session);
    } catch (reason) {
      setError(message(reason, "Could not create session"));
    }
  }

  async function selectSession(session: Session) {
    try {
      await loadSession(session);
    } catch (reason) {
      setError(message(reason, "Could not load session"));
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
          if (isTerminalRunEvent(runEvent.type)) {
            void finishRun(run.run_id).catch((reason: unknown) =>
              setError(message(reason, "Could not refresh completed run")),
            );
          }
        },
        () => setError("Event stream disconnected; the Run may still be active."),
      );
    } catch (reason) {
      setError(message(reason, "Could not start run"));
      setActiveRun(null);
    }
  }

  async function answerApproval(outcome: "allow_once" | "allow_session" | "deny") {
    if (!activeRun || !approval) return;
    try {
      await api.approve(activeRun.run_id, approval.request_id, outcome);
      setApproval(null);
    } catch (reason) {
      setError(message(reason, "Could not answer approval"));
    }
  }

  async function updateMode(mode: ApprovalMode) {
    if (!selected) return;
    try {
      setSelected(await api.updateMode(selected.session_id, mode));
      await refreshSessions();
    } catch (reason) {
      setError(message(reason, "Could not update approval mode"));
    }
  }

  async function updateModel(model: string) {
    if (!selected) return;
    try {
      setSelected(await api.updateModel(selected.session_id, model));
      await refreshSessions();
    } catch (reason) {
      setError(message(reason, "Could not update model"));
    }
  }

  async function selectRun(run: Run) {
    try {
      const value = await api.trace(run.run_id);
      setTrace(value);
      setEvents(value.events);
    } catch (reason) {
      setError(message(reason, "Could not load run trace"));
    }
  }

  async function cancelRun() {
    if (!activeRun) return;
    try {
      await api.cancelRun(activeRun.run_id);
    } catch (reason) {
      setError(message(reason, "Could not stop run"));
    }
  }

  return {
    serverOnline, sessions, selected, messages, runs, events, trace, models, providers,
    providerSettings,
    activeRun, approval, workspace, prompt, error, setWorkspace, setPrompt,
    createSession, selectSession, submitPrompt, answerApproval, updateMode, updateModel,
    selectRun, cancelRun, refreshConfiguration,
  };
}
