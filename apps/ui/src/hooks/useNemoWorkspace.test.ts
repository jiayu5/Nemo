import { act, renderHook, waitFor, cleanup } from "@testing-library/react";
import type { FormEvent } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { useNemoWorkspace } from "./useNemoWorkspace";
import type { ImageRef, Run } from "../types";

vi.mock("../api", () => ({
  defaultWorkspace: vi.fn(async () => "."),
  chooseWorkspace: vi.fn(),
  subscribeToRun: vi.fn(() => ({ close: vi.fn() })),
  api: { health: vi.fn(), sessions: vi.fn(), providerSettings: vi.fn(), models: vi.fn(), providers: vi.fn(),
    resumeSession: vi.fn(), messages: vi.fn(), runs: vi.fn(), uploadImage: vi.fn(), startRun: vi.fn() },
}));

const session = { session_id: "one", workspace: "/project", model: "vision", approval_mode: "ask" as const,
  created_at: "", updated_at: "", message_count: 0 };
const image: ImageRef = { asset_id: "a".repeat(64), mime_type: "image/png", size_bytes: 10, width: 2, height: 2 };
const event = { preventDefault: vi.fn() } as unknown as FormEvent;

beforeEach(() => {
  vi.mocked(api.health).mockResolvedValue({ status: "ok" });
  vi.mocked(api.sessions).mockResolvedValue([session]);
  vi.mocked(api.providerSettings).mockResolvedValue({ config_exists: true, default: "vision", providers: [] });
  vi.mocked(api.models).mockResolvedValue([{ selection: "vision", kind: "model", model_name: "vision", model_id: "vision",
    provider_id: "demo", protocol: "openai_compatible", capabilities: ["image_input"], is_default: true }]);
  vi.mocked(api.providers).mockResolvedValue([]);
  vi.mocked(api.resumeSession).mockResolvedValue(session);
  vi.mocked(api.messages).mockResolvedValue([]);
  vi.mocked(api.runs).mockResolvedValue([]);
  vi.mocked(api.uploadImage).mockResolvedValue(image);
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("sends image-only messages in upload order and keeps drafts when the server rejects", async () => {
  const { result } = renderHook(useNemoWorkspace);
  await waitFor(() => expect(result.current.selected?.session_id).toBe("one"));
  vi.mocked(api.uploadImage).mockResolvedValueOnce(image).mockResolvedValueOnce({ ...image, asset_id: "b".repeat(64) });
  await act(async () => { await result.current.addImages([
    new File(["a"], "a.png", { type: "image/png" }), new File(["b"], "b.png", { type: "image/png" }),
  ]); });
  expect(result.current.images.map((item) => item.asset_id)).toEqual(["a".repeat(64), "b".repeat(64)]);
  vi.mocked(api.startRun).mockRejectedValueOnce(new Error("Model cannot see images"));
  await act(async () => { await result.current.submitPrompt(event); });
  expect(api.startRun).toHaveBeenCalledWith("one", "", result.current.images);
  expect(result.current.images).toHaveLength(2);
  expect(result.current.messages).toEqual([]);
  expect(result.current.error).toBe("Model cannot see images");
});

it("prevents sending before uploads finish and prevents duplicate submissions", async () => {
  const { result } = renderHook(useNemoWorkspace);
  await waitFor(() => expect(result.current.selected).not.toBeNull());
  let finishUpload!: (value: ImageRef) => void;
  vi.mocked(api.uploadImage).mockImplementationOnce(() => new Promise((resolve) => { finishUpload = resolve; }));
  let uploading!: Promise<void>;
  act(() => { uploading = result.current.addImages([new File(["a"], "a.png", { type: "image/png" })]); });
  act(() => result.current.setPrompt("Compare"));
  await act(async () => { await result.current.submitPrompt(event); });
  expect(api.startRun).not.toHaveBeenCalled();
  await act(async () => { finishUpload(image); await uploading; });
  let finishStart!: (value: Run) => void;
  vi.mocked(api.startRun).mockImplementationOnce(() => new Promise((resolve) => { finishStart = resolve; }));
  let starting!: Promise<void>;
  act(() => { starting = result.current.submitPrompt(event); });
  await act(async () => { await result.current.submitPrompt(event); });
  expect(api.startRun).toHaveBeenCalledTimes(1);
  await act(async () => { finishStart({ run_id: "run", session_id: "one", status: "queued" } as Run); await starting; });
  expect(result.current.images).toEqual([]);
  expect(result.current.messages[0]).toMatchObject({ content: "Compare", images: [image] });
});

it("rejects unsupported models and too many images before uploading", async () => {
  vi.mocked(api.models).mockResolvedValue([]);
  const { result } = renderHook(useNemoWorkspace);
  await waitFor(() => expect(result.current.selected).not.toBeNull());
  await act(async () => { await result.current.addImages([new File(["a"], "a.png", { type: "image/png" })]); });
  expect(api.uploadImage).not.toHaveBeenCalled();
  expect(result.current.error).toContain("vision model");
});
