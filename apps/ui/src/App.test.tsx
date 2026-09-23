import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    {
      selection: "fast",
      kind: "alias",
      model_name: "demo-model",
      model_id: "demo-v1",
      provider_id: "demo",
      protocol: "openai_compatible",
      capabilities: ["tool_calling"],
      is_default: false,
    },
    {
      selection: "demo-model",
      kind: "model",
      model_name: "demo-model",
      model_id: "demo-v1",
      provider_id: "demo",
      protocol: "openai_compatible",
      capabilities: ["tool_calling"],
      is_default: false,
    },
  ],
  "/api/providers": [
    {
      provider_id: "demo",
      protocol: "openai_compatible",
      base_url: "https://example.test/v1",
      api_key_env: "DEMO_API_KEY",
      secret_configured: true,
      secret_source: "file",
      proxy_env: null,
      proxy_configured: false,
      proxy_source: null,
      timeout_seconds: 60,
      models: ["demo-model"],
    },
  ],
  "/api/settings/providers": {
    config_exists: true,
    default: "chat",
    providers: [
      {
        provider_id: "demo",
        protocol: "openai_compatible",
        base_url: "https://example.test/v1",
        api_key_env: "DEMO_API_KEY",
        api_key_source: "file",
        proxy_env: null,
        proxy_source: null,
        timeout_seconds: 60,
        models: [
          {
            model_name: "demo-model",
            model_id: "demo-v1",
            capabilities: ["tool_calling"],
            is_default: true,
          },
          {
            model_name: "demo-reasoner",
            model_id: "demo-reasoner-v1",
            capabilities: [],
            is_default: false,
          },
        ],
      },
    ],
  },
  "/api/settings/providers/demo/validate": {
    status: "valid",
    provider: {
      provider_id: "demo",
      protocol: "openai_compatible",
      base_url: "https://example.test/v1",
      api_key_env: "DEMO_API_KEY",
      api_key_source: "file",
      proxy_env: null,
      proxy_source: null,
      timeout_seconds: 60,
      models: [],
    },
    writes: ["config"],
  },
  "/api/settings/providers/new-provider": {
    status: "saved",
    provider: {
      provider_id: "new-provider",
      protocol: "openai_compatible",
      base_url: "https://new.example.test/v1",
      api_key_env: "NEW_PROVIDER_API_KEY",
      api_key_source: "file",
      proxy_env: null,
      proxy_source: null,
      timeout_seconds: 60,
      models: [
        {
          model_name: "new-chat",
          model_id: "new-chat-v1",
          capabilities: ["tool_calling"],
          is_default: true,
        },
      ],
    },
    writes: ["config", "secrets"],
  },
  "/api/settings/providers/demo": {
    status: "saved",
    provider: {
      provider_id: "demo",
      protocol: "openai_compatible",
      base_url: "https://example.test/v1",
      api_key_env: "DEMO_API_KEY",
      api_key_source: "file",
      proxy_env: null,
      proxy_source: null,
      timeout_seconds: 60,
      models: [],
    },
    writes: ["config"],
  },
};
let runStartResponse: unknown = null;
let runStartStatus = 200;

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
        if (init?.method === "DELETE" && path === "/api/sessions/session-1") {
          responses["/api/sessions"] = [];
          return new Response(null, { status: 204 });
        }
        const body =
          init?.method === "DELETE" && path === "/api/settings/providers/demo"
            ? { status: "deleted", provider_id: "demo", removed_models: ["demo-model"], default: "backup" }
            : init?.method === "POST" && path.endsWith("/runs") && runStartResponse
            ? runStartResponse
            : responses[path];
        return new Response(JSON.stringify(body), {
          status: body === undefined ? 404 : init?.method === "POST" && path.endsWith("/runs")
            ? runStartStatus : 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  });

  afterEach(() => {
    cleanup();
    responses["/api/sessions"] = [];
    delete responses["/api/sessions/session-1/messages"];
    delete responses["/api/sessions/session-1/runs"];
    delete responses["/api/runs/run-1/trace"];
    runStartResponse = null;
    runStartStatus = 200;
    vi.unstubAllGlobals();
  });

  it("shows server state and the empty session workflow", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("Server online")).toBeInTheDocument());
    expect(screen.getByText("Create a session to begin.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Connections" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "New session" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Model" }).closest(".composer-toolbar"))
      .not.toBeNull();
    expect(screen.getByRole("combobox", { name: "Approval" }).closest(".composer-toolbar"))
      .not.toBeNull();
    const modelOptions = screen.getByRole("combobox", { name: "Model" }).querySelectorAll("option");
    expect(Array.from(modelOptions, (option) => option.textContent)).toEqual([
      "demo-model · demo",
    ]);
  });

  it("requires confirmation before deleting a session", async () => {
    responses["/api/sessions"] = [{
      session_id: "session-1", workspace: "/workspace", model: "chat", approval_mode: "ask",
      created_at: "2026-09-21T00:00:00Z", updated_at: "2026-09-21T00:00:00Z", message_count: 0,
    }];
    responses["/api/sessions/session-1/messages"] = [];
    responses["/api/sessions/session-1/runs"] = [];
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete session session-" }));
    expect(fetch).not.toHaveBeenCalledWith("/api/sessions/session-1", expect.objectContaining({ method: "DELETE" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete session" }));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/sessions/session-1", expect.objectContaining({ method: "DELETE" }),
    ));
    await waitFor(() => expect(screen.getByText("Create a session to begin.")).toBeInTheDocument());
  });

  it("validates provider settings without exposing an existing secret", async () => {
    render(<App />);
    await screen.findByText("Server online");
    fireEvent.click(screen.getAllByRole("button", { name: "Connections" })[0]);
    expect(await screen.findByRole("heading", { name: "demo" })).toBeInTheDocument();
    expect(screen.getAllByText("file").length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("New API key")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Validate" }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/api/settings/providers/demo/validate",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(await screen.findByText("Valid. Will update config.")).toBeInTheDocument();
  });

  it("keeps the add-provider form open and saves a new provider", async () => {
    render(<App />);
    await screen.findByText("Server online");
    fireEvent.click(screen.getAllByRole("button", { name: "Connections" })[0]);
    await screen.findByRole("heading", { name: "demo" });

    fireEvent.click(screen.getByRole("button", { name: /Add provider/i }));
    expect(await screen.findByRole("heading", { name: "Add a model provider" })).toBeInTheDocument();

    const providerId = screen.getByPlaceholderText("my-provider");
    expect(providerId).toBeEnabled();
    fireEvent.change(providerId, { target: { value: "new-provider" } });
    expect(screen.getByLabelText("API key variable")).toHaveValue("NEW_PROVIDER_API_KEY");
    fireEvent.change(screen.getByPlaceholderText("https://api.example.com/v1"), {
      target: { value: "https://new.example.test/v1" },
    });
    fireEvent.change(screen.getByPlaceholderText("my-chat-model"), {
      target: { value: "new-chat" },
    });
    fireEvent.change(screen.getByPlaceholderText("model-v1"), {
      target: { value: "new-chat-v1" },
    });
    fireEvent.change(screen.getByLabelText("New API key"), {
      target: { value: "secret-value" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm add" }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/api/settings/providers/new-provider",
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"model_name":"new-chat"'),
        }),
      ),
    );
  });

  it("requires confirmation before deleting a provider", async () => {
    render(<App />);
    await screen.findByText("Server online");
    fireEvent.click(screen.getAllByRole("button", { name: "Connections" })[0]);
    await screen.findByRole("heading", { name: "demo" });

    const deleteButton = screen.getByRole("button", { name: "Delete provider" });
    const saveButton = screen.getByRole("button", { name: "Save changes" });
    expect(deleteButton.parentElement).toBe(saveButton.parentElement);
    fireEvent.click(deleteButton);
    expect(screen.getByText("Delete demo?")).toBeInTheDocument();
    expect(screen.getByText(/Saved secret values are retained/)).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalledWith(
      "/api/settings/providers/demo",
      expect.objectContaining({ method: "DELETE" }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Delete permanently" }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/api/settings/providers/demo",
        expect.objectContaining({ method: "DELETE" }),
      ),
    );
  });

  it("shows every provider model and adds another model", async () => {
    render(<App />);
    await screen.findByText("Server online");
    fireEvent.click(screen.getAllByRole("button", { name: "Connections" })[0]);
    await screen.findByRole("heading", { name: "demo" });
    expect(screen.getByText("demo-model")).toBeInTheDocument();
    expect(screen.getByText("demo-reasoner")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "+ Add model" }));
    fireEvent.change(screen.getByPlaceholderText("my-chat-model"), {
      target: { value: "demo-pro" },
    });
    fireEvent.change(screen.getByPlaceholderText("model-v1"), {
      target: { value: "demo-pro-v1" },
    });
    const addModel = document.querySelector<HTMLButtonElement>(".settings-actions .primary");
    expect(addModel).not.toBeNull();
    expect(addModel).toHaveTextContent("Add model");
    fireEvent.click(addModel!);

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/api/settings/providers/demo",
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"model_name":"demo-pro"'),
        }),
      ),
    );
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

  it("restores the prompt when another client already runs the session", async () => {
    responses["/api/sessions"] = [{
      session_id: "session-1", workspace: "/workspace", model: "demo-model",
      approval_mode: "ask", created_at: "2026-09-21T00:00:00Z",
      updated_at: "2026-09-21T00:00:00Z", message_count: 0,
    }];
    responses["/api/sessions/session-1/messages"] = [];
    responses["/api/sessions/session-1/runs"] = [];
    runStartResponse = { detail: "session already has an active run" };
    runStartStatus = 409;

    render(<App />);
    const input = await screen.findByPlaceholderText("Ask anything");
    fireEvent.change(input, { target: { value: "second task" } });
    fireEvent.click(screen.getByRole("button", { name: "Run task" }));
    expect(await screen.findByText("session already has an active run"))
      .toBeInTheDocument();
    expect(input).toHaveValue("second task");
    expect(screen.getByLabelText("Conversation messages")).not.toHaveTextContent("second task");
  });

  it("shows model deltas before the run finishes", async () => {
    const listeners: Record<string, (event: MessageEvent<string>) => void> = {};
    vi.stubGlobal("EventSource", class {
      onerror: (() => void) | null = null;
      addEventListener(type: string, callback: (event: MessageEvent<string>) => void) {
        listeners[type] = callback;
      }
      close() {}
    });
    responses["/api/sessions"] = [{
      session_id: "session-1", workspace: "/workspace", model: "demo-model",
      approval_mode: "ask", created_at: "2026-09-21T00:00:00Z",
      updated_at: "2026-09-21T00:00:00Z", message_count: 0,
    }];
    responses["/api/sessions/session-1/messages"] = [];
    responses["/api/sessions/session-1/runs"] = [];
    runStartResponse = {
      run_id: "run-1", session_id: "session-1", status: "queued", max_steps: 10,
      output: null, error: null, created_at: "2026-09-21T00:00:00Z",
      started_at: null, finished_at: null, model_selection: null, model_name: null,
      model_id: null, model_protocol: null, model_provider: null,
    };

    render(<App />);
    const input = await screen.findByPlaceholderText("Ask anything");
    fireEvent.change(input, { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Run task" }));
    await waitFor(() => expect(listeners["model.delta"]).toBeDefined());
    const delta = { run_id: "run-1", seq: 3, step: 1, type: "model.delta",
      timestamp: "2026-09-21T00:00:00Z", payload: { text: "Hello from the stream" } };
    listeners["model.delta"](new MessageEvent("model.delta", { data: JSON.stringify(delta) }));
    expect(await screen.findByLabelText("Streaming assistant response"))
      .toHaveTextContent("Hello from the stream");
    expect(screen.queryByText("model.delta")).not.toBeInTheDocument();
  });

  it("shows reasoning separately during streaming", async () => {
    const listeners: Record<string, (event: MessageEvent<string>) => void> = {};
    vi.stubGlobal("EventSource", class {
      onerror: (() => void) | null = null;
      addEventListener(type: string, callback: (event: MessageEvent<string>) => void) {
        listeners[type] = callback;
      }
      close() {}
    });
    responses["/api/sessions"] = [{
      session_id: "session-1", workspace: "/workspace", model: "demo-model",
      approval_mode: "ask", created_at: "2026-09-21T00:00:00Z",
      updated_at: "2026-09-21T00:00:00Z", message_count: 0,
    }];
    responses["/api/sessions/session-1/messages"] = [];
    responses["/api/sessions/session-1/runs"] = [];
    runStartResponse = {
      run_id: "run-1", session_id: "session-1", status: "queued", max_steps: 10,
      output: null, error: null, created_at: "2026-09-21T00:00:00Z",
      started_at: null, finished_at: null, model_selection: null, model_name: null,
      model_id: null, model_protocol: null, model_provider: null,
    };
    render(<App />);
    fireEvent.change(await screen.findByPlaceholderText("Ask anything"), { target: { value: "Hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Run task" }));
    await waitFor(() => expect(listeners["model.reasoning_delta"]).toBeDefined());
    const reasoning = { run_id: "run-1", seq: 3, step: 1, type: "model.reasoning_delta",
      timestamp: "2026-09-21T00:00:00Z", payload: { text: "Check facts" } };
    listeners["model.reasoning_delta"](new MessageEvent("model.reasoning_delta", { data: JSON.stringify(reasoning) }));
    expect(await screen.findByText("Check facts")).toBeInTheDocument();
    expect(screen.getByText("Reasoning · generating")).toBeInTheDocument();
    expect(screen.queryByText("model.reasoning_delta")).not.toBeInTheDocument();
  });

  it("restores saved reasoning inside the assistant message", async () => {
    responses["/api/sessions"] = [{
      session_id: "session-1", workspace: "/workspace", model: "demo-model",
      approval_mode: "ask", created_at: "2026-09-21T00:00:00Z",
      updated_at: "2026-09-21T00:00:00Z", message_count: 2,
    }];
    responses["/api/sessions/session-1/messages"] = [
      { role: "user", content: "Question", tool_calls: [], tool_result: null },
      { role: "assistant", content: "Answer", reasoning_content: "Check facts",
        tool_calls: [], tool_result: null },
    ];
    responses["/api/sessions/session-1/runs"] = [];
    render(<App />);
    expect(await screen.findByText("Reasoning")).toBeInTheDocument();
    expect(screen.getByText("Check facts")).toBeInTheDocument();
    expect(screen.getByText("Answer")).toBeInTheDocument();
  });

  it("restores a running response after a page reload", async () => {
    const urls: string[] = [];
    vi.stubGlobal("EventSource", class {
      onerror: (() => void) | null = null;
      constructor(url: string) { urls.push(url); }
      addEventListener() {}
      close() {}
    });
    responses["/api/sessions"] = [{
      session_id: "session-1", workspace: "/workspace", model: "demo-model",
      approval_mode: "ask", created_at: "2026-09-21T00:00:00Z",
      updated_at: "2026-09-21T00:00:00Z", message_count: 1,
    }];
    responses["/api/sessions/session-1/messages"] = [{
      role: "user", content: "Say hello", tool_calls: [], tool_result: null,
    }];
    responses["/api/sessions/session-1/runs"] = [{
      run_id: "run-1", session_id: "session-1", status: "running",
    }];
    responses["/api/runs/run-1/trace"] = {
      run: { run_id: "run-1", session_id: "session-1", status: "running" },
      duration_ms: null, steps: [], model_calls: [], tool_calls: [],
      events: [{ run_id: "run-1", seq: 7, step: 1, type: "model.delta",
        timestamp: "2026-09-21T00:00:00Z", payload: { text: "Hello again" } }],
    };

    render(<App />);
    expect(await screen.findByLabelText("Streaming assistant response"))
      .toHaveTextContent("Hello again");
    expect(urls).toEqual(["/api/runs/run-1/events?after=7"]);
  });

  it("does not resubscribe when a run finishes during session reload", async () => {
    const urls: string[] = [];
    vi.stubGlobal("EventSource", class {
      onerror: (() => void) | null = null;
      constructor(url: string) { urls.push(url); }
      addEventListener() {}
      close() {}
    });
    responses["/api/sessions"] = [{
      session_id: "session-1", workspace: "/workspace", model: "demo-model",
      approval_mode: "ask", created_at: "2026-09-21T00:00:00Z",
      updated_at: "2026-09-21T00:00:00Z", message_count: 1,
    }];
    responses["/api/sessions/session-1/messages"] = [];
    responses["/api/sessions/session-1/runs"] = [{
      run_id: "run-1", session_id: "session-1", status: "running",
    }];
    responses["/api/runs/run-1/trace"] = {
      run: { run_id: "run-1", session_id: "session-1", status: "completed" },
      duration_ms: 1, steps: [], model_calls: [], tool_calls: [],
      events: [{ run_id: "run-1", seq: 3, step: 1, type: "run.completed",
        timestamp: "2026-09-21T00:00:00Z", payload: {} }],
    };

    render(<App />);
    await screen.findByPlaceholderText("Ask anything");
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(
      ([path]) => String(path) === "/api/sessions/session-1/messages",
    )).toHaveLength(2));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Stop" })).not.toBeInTheDocument());
    expect(urls).toEqual([]);
  });
});
