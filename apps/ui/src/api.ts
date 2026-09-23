import { invoke, isTauri } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import type {
  ApprovalMode,
  Message,
  ModelOption,
  Provider,
  ProviderDeleteResult,
  ProviderSettings,
  ProviderSettingsDraft,
  ProviderSettingsResult,
  Run,
  RunEvent,
  Session,
  Trace,
} from "./types";
import { isTerminalRunEvent, RUN_EVENT_TYPES } from "./events";

const API = "/api";

interface DesktopConnection {
  port: number;
  token: string;
  workspace: string;
}

let desktopConnection: Promise<DesktopConnection> | null = null;

function desktop(): Promise<DesktopConnection> {
  desktopConnection ??= invoke<DesktopConnection>("desktop_connection").catch((error: unknown) => {
    desktopConnection = null;
    throw error;
  });
  return desktopConnection;
}

async function endpoint(): Promise<{ base: string; headers: HeadersInit }> {
  if (!isTauri()) return { base: API, headers: {} };
  const { port, token } = await desktop();
  return { base: `http://127.0.0.1:${port}`, headers: { "X-Nemo-Token": token } };
}

/**
 * Default workspace for a new session: the sidecar's own directory is meaningless
 * inside a bundle, so the desktop shell reports the user's home directory instead.
 */
export async function defaultWorkspace(): Promise<string> {
  if (!isTauri()) return ".";
  try {
    return (await desktop()).workspace;
  } catch {
    return ".";
  }
}

export async function chooseWorkspace(current: string): Promise<string | null> {
  if (!isTauri()) return null;
  return open({
    directory: true,
    multiple: false,
    title: "Choose a workspace",
    ...(current.startsWith("/") ? { defaultPath: current } : {}),
  });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const target = await endpoint();
  const response = await fetch(`${target.base}${path}`, {
    ...init,
    headers: {
      ...target.headers,
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (Array.isArray(body.detail)) {
        const messages = body.detail
          .map((item) =>
            typeof item === "object" && item !== null && "message" in item
              ? String(item.message)
              : "",
          )
          .filter(Boolean);
        if (messages.length) detail = messages.join("; ");
      }
    } catch {
      // Keep the status-only fallback; response bodies are not always JSON.
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
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
  deleteSession: (sessionId: string) =>
    request<void>(`/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" }),
  messages: (sessionId: string) =>
    request<Message[]>(`/sessions/${sessionId}/messages`),
  runs: (sessionId: string) => request<Run[]>(`/sessions/${sessionId}/runs`),
  models: () => request<ModelOption[]>("/models"),
  providers: () => request<Provider[]>("/providers"),
  providerSettings: () => request<ProviderSettings>("/settings/providers"),
  validateProviderSettings: (providerId: string, draft: ProviderSettingsDraft) =>
    request<ProviderSettingsResult>(
      `/settings/providers/${encodeURIComponent(providerId)}/validate`,
      { method: "POST", body: JSON.stringify(draft) },
    ),
  saveProviderSettings: (providerId: string, draft: ProviderSettingsDraft) =>
    request<ProviderSettingsResult>(`/settings/providers/${encodeURIComponent(providerId)}`, {
      method: "PUT",
      body: JSON.stringify(draft),
    }),
  deleteProviderSettings: (providerId: string) =>
    request<ProviderDeleteResult>(`/settings/providers/${encodeURIComponent(providerId)}`, {
      method: "DELETE",
    }),
  testProvider: (providerId: string, model: string | null) =>
    request<{ status: "ok"; latency_ms: number }>(
      `/providers/${encodeURIComponent(providerId)}/test`,
      { method: "POST", body: JSON.stringify({ model }) },
    ),
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

export interface RunSubscription {
  close(): void;
}

export function subscribeToRun(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onError: () => void,
  after = 0,
): RunSubscription {
  if (isTauri()) return subscribeDesktop(runId, onEvent, onError, after);
  const source = new EventSource(`${API}/runs/${runId}/events?after=${after}`);
  for (const type of RUN_EVENT_TYPES) {
    source.addEventListener(type, (message) => {
      onEvent(JSON.parse((message as MessageEvent<string>).data) as RunEvent);
    });
  }
  source.onerror = onError;
  return source;
}

function subscribeDesktop(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onError: () => void,
  after: number,
): RunSubscription {
  const abort = new AbortController();
  let cursor = after;
  let finished = false;

  const readEvents = async () => {
    while (!abort.signal.aborted && !finished) {
      try {
        const target = await endpoint();
        const response = await fetch(`${target.base}/runs/${runId}/events?after=${cursor}`, {
          headers: target.headers,
          signal: abort.signal,
        });
        if (!response.ok || !response.body) throw new Error("Event stream unavailable");
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!abort.signal.aborted) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          buffer = buffer.replace(/\r\n/g, "\n");
          let separator = buffer.indexOf("\n\n");
          while (separator !== -1) {
            const block = buffer.slice(0, separator);
            buffer = buffer.slice(separator + 2);
            const data = block.split("\n")
              .filter((line) => line.startsWith("data:"))
              .map((line) => line.slice(5).trimStart()).join("\n");
            if (data) {
              const event = JSON.parse(data) as RunEvent;
              cursor = Math.max(cursor, event.seq);
              onEvent(event);
              if (isTerminalRunEvent(event.type)) {
                finished = true;
                break;
              }
            }
            separator = buffer.indexOf("\n\n");
          }
          if (finished) break;
        }
        if (finished || abort.signal.aborted) return;
        throw new Error("Event stream ended before run completed");
      } catch {
        if (abort.signal.aborted || finished) return;
        onError();
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    }
  };

  void readEvents();
  return { close: () => abort.abort() };
}

export function formatDuration(value: number | null): string {
  if (value === null) return "—";
  if (value < 1000) return `${Math.round(value)} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}
