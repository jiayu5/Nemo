import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api, defaultWorkspace, subscribeToRun } from "../api";
import type { RunSubscription } from "../api";
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
  const [liveText, setLiveText] = useState("");
  const [liveReasoning, setLiveReasoning] = useState("");
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
  const streamRef = useRef<RunSubscription | null>(null);

  async function finishRun(runId: string, sessionId: string) {
    streamRef.current?.close();
    streamRef.current = null;
    setActiveRun(null);
    setApproval(null);
    const [history, sessionRuns, runTrace, sessionItems] = await Promise.all([
      api.messages(sessionId),
      api.runs(sessionId),
      api.trace(runId),
      api.sessions(),
    ]);
    setMessages(history);
    setLiveText("");
    setLiveReasoning("");
    setRuns(sessionRuns);
    setTrace(runTrace);
    setEvents(runTrace.events.filter((item) => item.type !== "model.delta" && item.type !== "model.reasoning_delta"));
    setSessions(sessionItems);
    setSelected(sessionItems.find((item) => item.session_id === sessionId) ?? null);
  }

  function watchRun(run: Run, after = 0) {
    streamRef.current?.close();
    streamRef.current = subscribeToRun(
      run.run_id,
      (runEvent) => {
        if (runEvent.type === "model.delta") {
          const text = runEvent.payload.text;
          if (typeof text === "string") setLiveText((current) => current + text);
        } else if (runEvent.type === "model.reasoning_delta") {
          const text = runEvent.payload.text;
          if (typeof text === "string") setLiveReasoning((current) => current + text);
        } else {
          setEvents((current) => [...current, runEvent]);
        }
        if (runEvent.type === "approval.requested") {
          setApproval(runEvent.payload as unknown as ApprovalRequest);
        }
        if (isTerminalRunEvent(runEvent.type)) {
          void finishRun(run.run_id, run.session_id).catch((reason: unknown) =>
            setError(message(reason, "Could not refresh completed run")),
          );
        }
      },
      () => setError("Event stream disconnected; reconnecting to the Run."),
      after,
    );
  }

  const loadSession = useCallback(async (session: Session) => {
    streamRef.current?.close();
    streamRef.current = null;
    setActiveRun(null);
    setApproval(null);
    setSelected(session);
    setError(null);
    const [history, sessionRuns] = await Promise.all([
      api.messages(session.session_id),
      api.runs(session.session_id),
    ]);
    setMessages(history);
    setLiveText("");
    setLiveReasoning("");
    setRuns(sessionRuns);
    const newest = sessionRuns[0];
    if (newest) {
      const newestTrace = await api.trace(newest.run_id);
      setTrace(newestTrace);
      setEvents(newestTrace.events.filter((item) => item.type !== "model.delta" && item.type !== "model.reasoning_delta"));
      if (newest.status === "queued" || newest.status === "running") {
        if (newestTrace.run.status !== "queued" && newestTrace.run.status !== "running") {
          await finishRun(newest.run_id, session.session_id);
          return;
        }
        setLiveText(newestTrace.events
          .filter((item) => item.type === "model.delta")
          .map((item) => item.payload.text)
          .filter((value): value is string => typeof value === "string")
          .join(""));
        setLiveReasoning(newestTrace.events
          .filter((item) => item.type === "model.reasoning_delta")
          .map((item) => item.payload.text)
          .filter((value): value is string => typeof value === "string")
          .join(""));
        setActiveRun(newest);
        const pending = [...newestTrace.events].reverse().find(
          (item) => item.type === "approval.requested",
        );
        const answered = pending && newestTrace.events.some(
          (item) => item.type === "approval.resolved" &&
            item.payload.request_id === pending.payload.request_id,
        );
        if (pending && !answered) setApproval(pending.payload as unknown as ApprovalRequest);
        watchRun(newest, newestTrace.events.at(-1)?.seq ?? 0);
      }
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
    void defaultWorkspace().then((value) => {
      if (!cancelled) setWorkspace((current) => (current === "." ? value : current));
    });
    return () => { cancelled = true; };
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

  async function submitPrompt(event: FormEvent) {
    event.preventDefault();
    if (!selected || !prompt.trim() || activeRun) return;
    const text = prompt.trim();
    setPrompt("");
    setLiveText("");
    setLiveReasoning("");
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
      watchRun(run);
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
      setEvents(value.events.filter((item) => item.type !== "model.delta" && item.type !== "model.reasoning_delta"));
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
    serverOnline, sessions, selected, messages, liveText, liveReasoning, runs, events, trace, models, providers,
    providerSettings,
    activeRun, approval, workspace, prompt, error, setWorkspace, setPrompt,
    createSession, selectSession, submitPrompt, answerApproval, updateMode, updateModel,
    selectRun, cancelRun, refreshConfiguration,
  };
}
