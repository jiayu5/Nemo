import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, cleanup } from "@testing-library/react";
import { MemoryPanel } from "./MemoryPanel";
import { api } from "../api";
import type { MemoryEntry, Session } from "../types";

vi.mock("../api", () => ({ api: {
  memories: vi.fn(), saveMemory: vi.fn(), forgetMemory: vi.fn(), setMemoryEnabled: vi.fn(), messages: vi.fn(),
} }));
const session: Session = { session_id: "a", workspace: "/project", model: null, approval_mode: "ask",
  created_at: "", updated_at: "", message_count: 0 };
const entry: MemoryEntry = { id: "entry", scope: "project", title: "Response style", content: "Lead with conclusion",
  kind: "feedback", revision: "rev", updated_at: "2026-10-08T00:00:00Z", source_session_id: "source",
  source_message_index: 0, source_run_id: "run" };

describe("Memory manager", () => {
  beforeEach(() => {
    cleanup(); vi.resetAllMocks();
    HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    vi.mocked(api.memories).mockResolvedValue({ enabled: true, entries: [entry] });
  });
  it("edits with the current revision and requires a separate forget confirmation", async () => {
    render(<MemoryPanel session={session} onClose={() => {}} />);
    await screen.findByText("Response style");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Memory content"), { target: { value: "Explain in detail" } });
    fireEvent.click(screen.getByRole("button", { name: "Save memory" }));
    await waitFor(() => expect(api.saveMemory).toHaveBeenCalledWith("a", expect.objectContaining({
      id: "entry", revision: "rev", content: "Explain in detail", scope: "project",
    })));
    await waitFor(() => expect(screen.getByRole("button", { name: "Forget" })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "Forget" }));
    expect(api.forgetMemory).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm forget" }));
    await waitFor(() => expect(api.forgetMemory).toHaveBeenCalledWith("a", entry));
  });
  it("shows the source and surfaces stale-update errors without losing the draft", async () => {
    vi.mocked(api.messages).mockResolvedValue([{ role: "user", content: "Please remember this", tool_calls: [], tool_result: null }]);
    render(<MemoryPanel session={session} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "View source" }));
    await screen.findByText("Please remember this");
    await waitFor(() => expect(screen.getByRole("button", { name: "Edit" })).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    vi.mocked(api.saveMemory).mockRejectedValue(new Error("memory changed; read again"));
    fireEvent.click(screen.getByRole("button", { name: "Save memory" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("memory changed");
    expect(screen.getByLabelText("Memory content")).toHaveValue(entry.content);
  });
  it("turns off recall without removing entries", async () => {
    render(<MemoryPanel session={session} onClose={() => {}} />);
    fireEvent.click(await screen.findByRole("checkbox"));
    await waitFor(() => expect(api.setMemoryEnabled).toHaveBeenCalledWith(false));
    expect(api.forgetMemory).not.toHaveBeenCalled();
  });
});
