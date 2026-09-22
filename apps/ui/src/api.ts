import type {
  ApprovalMode,
  Message,
  ModelOption,
  Provider,
  Run,
  RunEvent,
  Session,
  Trace,
} from "./types";
import { RUN_EVENT_TYPES } from "./events";

const API = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: init?.body
      ? { "Content-Type": "application/json", ...init.headers }
      : init?.headers,
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      detail = body.detail || detail;
    } catch {
      // Keep the status-only fallback; response bodies are not always JSON.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string }>("/health"),
  sessions: () => request<Session[]>("/sessions"),
  createSession: (workspace: string, model: string | null, approvalMode: ApprovalMode) =>
    request<Session>("/sessions", {
      method: "POST",
      body: JSON.stringify({ workspace, model, approval_mode: approvalMode }),
    }),
  messages: (sessionId: string) =>
    request<Message[]>(`/sessions/${sessionId}/messages`),
  runs: (sessionId: string) => request<Run[]>(`/sessions/${sessionId}/runs`),
  models: () => request<ModelOption[]>("/models"),
  providers: () => request<Provider[]>("/providers"),
  updateMode: (sessionId: string, approvalMode: ApprovalMode) =>
    request<Session>(`/sessions/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify({ approval_mode: approvalMode }),
    }),
  updateModel: (sessionId: string, model: string) =>
    request<Session>(`/sessions/${sessionId}/model`, {
      method: "PUT",
      body: JSON.stringify({ model }),
    }),
  startRun: (sessionId: string, prompt: string) =>
    request<Run>(`/sessions/${sessionId}/runs`, {
      method: "POST",
      body: JSON.stringify({ prompt, max_steps: 10 }),
    }),
  cancelRun: (runId: string) =>
    request<{ status: string }>(`/runs/${runId}/cancel`, { method: "POST" }),
  approve: (runId: string, requestId: string, outcome: string) =>
    request<{ status: string }>(`/runs/${runId}/approvals/${requestId}`, {
      method: "POST",
      body: JSON.stringify({ outcome }),
    }),
  trace: (runId: string) => request<Trace>(`/runs/${runId}/trace`),
};

export function subscribeToRun(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onError: () => void,
): EventSource {
  const source = new EventSource(`${API}/runs/${runId}/events`);
  for (const type of RUN_EVENT_TYPES) {
    source.addEventListener(type, (message) => {
      onEvent(JSON.parse((message as MessageEvent<string>).data) as RunEvent);
    });
  }
  source.onerror = onError;
  return source;
}

export function formatDuration(value: number | null): string {
  if (value === null) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}
