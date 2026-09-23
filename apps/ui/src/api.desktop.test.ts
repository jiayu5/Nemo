import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, chooseWorkspace, defaultWorkspace, subscribeToRun } from "./api";

const desktop = vi.hoisted(() => ({
  invoke: vi.fn(async () => ({
    port: 43210,
    token: "desktop-test-token",
    workspace: "/Users/desktop-user",
  })),
}));
const dialog = vi.hoisted(() => ({ open: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({
  isTauri: () => true,
  invoke: desktop.invoke,
}));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: dialog.open }));

describe("desktop API transport", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("starts new sessions in the workspace reported by the desktop shell", async () => {
    expect(await defaultWorkspace()).toBe("/Users/desktop-user");
    expect(desktop.invoke).toHaveBeenCalledWith("desktop_connection");
  });

  it("chooses a folder and leaves the path unchanged when cancelled", async () => {
    dialog.open.mockResolvedValueOnce("/Users/desktop-user/Projects/Nemo");
    expect(await chooseWorkspace("/Users/desktop-user")).toBe(
      "/Users/desktop-user/Projects/Nemo",
    );
    expect(dialog.open).toHaveBeenCalledWith({
      directory: true, multiple: false, title: "Choose a workspace",
      defaultPath: "/Users/desktop-user",
    });
    dialog.open.mockResolvedValueOnce(null);
    expect(await chooseWorkspace("/Users/desktop-user")).toBeNull();
  });

  it("sends the desktop token to the sidecar", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ status: "ok" }), {
      headers: { "Content-Type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetcher);
    expect(await api.health()).toEqual({ status: "ok" });
    expect(fetcher).toHaveBeenCalledWith("http://127.0.0.1:43210/health",
      expect.objectContaining({ headers: expect.objectContaining({
        "X-Nemo-Token": "desktop-test-token",
      }) }));
    vi.unstubAllGlobals();
  });

  it("parses authenticated SSE frames split across network chunks", async () => {
    const encoder = new TextEncoder();
    const runEvent = { run_id: "run-1", seq: 4, step: 1, type: "model.delta",
      timestamp: "2026-09-23T00:00:00Z", payload: { text: "Hello" } };
    const terminal = { ...runEvent, seq: 5, type: "run.completed", payload: {} };
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(`id: 4\r\nevent: model.delta\r\ndata: ${JSON.stringify(runEvent)}\r`));
        controller.enqueue(encoder.encode(`\n\r\nid: 5\nevent: run.completed\ndata: ${JSON.stringify(terminal)}\n\n`));
        controller.close();
      },
    });
    const fetcher = vi.fn(async () => new Response(stream, {
      headers: { "Content-Type": "text/event-stream" },
    }));
    vi.stubGlobal("fetch", fetcher);
    const received: string[] = [];
    const onError = vi.fn();
    const subscription = subscribeToRun("run-1", (event) => received.push(event.type), onError, 3);
    await vi.waitFor(() => expect(received).toEqual(["model.delta", "run.completed"]));
    expect(fetcher).toHaveBeenCalledWith("http://127.0.0.1:43210/runs/run-1/events?after=3",
      expect.objectContaining({ headers: expect.objectContaining({
        "X-Nemo-Token": "desktop-test-token",
      }) }));
    expect(onError).not.toHaveBeenCalled();
    subscription.close();
    vi.unstubAllGlobals();
  });
});
