import { render, screen, waitFor } from "@testing-library/react";
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

describe("App", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input);
        return new Response(JSON.stringify(responses[path]), {
          status: path in responses ? 200 : 404,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
  });

  afterEach(() => vi.unstubAllGlobals());

  it("shows server state and the empty session workflow", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByText("Server online")).toBeInTheDocument());
    expect(screen.getByText("Create a session to begin.")).toBeInTheDocument();
    expect(screen.getByText("demo")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New session" })).toBeInTheDocument();
  });
});
