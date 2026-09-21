import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const responses: Record<string, unknown> = {
  "/api/health": { status: "ok" },
  "/api/sessions": [],
  "/api/models": [
    {
      selection: "chat",
      kind: "profile",
      model_name: "demo-model",
      model_id: "demo-v1",
      provider_id: "demo",
      protocol: "openai_compatible",
      capabilities: ["tool_calling"],
      is_default: true,
    },
  ],
  "/api/providers": [
    {
      provider_id: "demo",
      protocol: "openai_compatible",
      base_url: "https://example.test/v1",
      api_key_env: "DEMO_API_KEY",
      secret_configured: true,
      timeout_seconds: 60,
      models: ["demo-model"],
    },
  ],
};
let runStartResponse: unknown = null;

describe("App", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "EventSource",
      class {
        onerror: (() => void) | null = null;
        addEventListener() {}
        close() {}
      },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const path = String(input);
        const body =
          init?.method === "POST" && path.endsWith("/runs") && runStartResponse
            ? runStartResponse
            : responses[path];
        return new Response(JSON.stringify(body), {
          status: body === undefined ? 404 : 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  });

  afterEach(() => {
    responses["/api/sessions"] = [];
    delete responses["/api/sessions/session-1/messages"];
    delete responses["/api/sessions/session-1/runs"];
    runStartResponse = null;
    vi.unstubAllGlobals();
  });

  it("shows server state and the empty session workflow", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("Server online")).toBeInTheDocument());
    expect(screen.getByText("Create a session to begin.")).toBeInTheDocument();
    expect(screen.getByText("demo")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New session" })).toBeInTheDocument();
  });

  it("sends with Enter and preserves Shift+Enter for a new line", async () => {
    const session = {
      session_id: "session-1",
      workspace: "/workspace",
      model: "chat",
      approval_mode: "ask",
      created_at: "2026-09-21T00:00:00Z",
      updated_at: "2026-09-21T00:00:00Z",
      message_count: 0,
    };
    responses["/api/sessions"] = [session];
    responses["/api/sessions/session-1/messages"] = [];
    responses["/api/sessions/session-1/runs"] = [];
    runStartResponse = {
      run_id: "run-1",
      session_id: "session-1",
      status: "queued",
      max_steps: 10,
      output: null,
      error: null,
      created_at: "2026-09-21T00:00:00Z",
      started_at: null,
      finished_at: null,
      model_selection: null,
      model_name: null,
      model_id: null,
      model_protocol: null,
      model_provider: null,
    };

    render(<App />);
    const input = await screen.findByPlaceholderText("Ask anything");
    fireEvent.change(input, { target: { value: "Inspect the project" } });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(fetch).not.toHaveBeenCalledWith(
      "/api/sessions/session-1/runs",
      expect.objectContaining({ method: "POST" }),
    );

    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/api/sessions/session-1/runs",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ prompt: "Inspect the project", max_steps: 10 }),
        }),
      ),
    );
  });
});
