import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SubagentView } from "./SubagentView";
import type { RunEvent } from "../types";

afterEach(cleanup);
function event(seq: number, type: string, payload: Record<string, unknown>, id = "child"): RunEvent {
  return { run_id: "parent", seq, step: 3, timestamp: "", type: "subagent.event",
    payload: { child_run_id: id, child_step: type === "subagent.queued" ? 0 : 1, child_type: type, child_payload: payload } };
}
it("shows only the selected child's live output, tool details and partial report", () => {
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  const onClose = vi.fn();
  const initial = [event(1, "subagent.queued", { task: "Inspect cancellation" }), event(2, "run.started", {}),
    event(3, "model.delta", { text: "Observed " }), event(4, "model.delta", { text: "cleanup" }),
    event(5, "model.delta", { text: "Other child's output" }, "other")];
  const view = render(<SubagentView runId="child" events={initial} onClose={onClose} />);
  expect(screen.getByRole("dialog")).toHaveAttribute("open");
  expect(screen.getByText("Inspect cancellation")).toBeVisible();
  expect(screen.getByText("Observed cleanup")).toBeVisible();
  expect(screen.queryByText("Other child's output")).toBeNull();
  view.rerender(<SubagentView runId="child" events={[...initial,
    event(6, "model.completed", { content: "Observed cleanup", tool_calls: [{ id: "read", name: "read_file", arguments: '{"path":"agent.py"}' }] }),
    event(7, "tool.started", { tool_call_id: "read", name: "read_file" }),
    event(8, "tool.completed", { tool_call_id: "read", name: "read_file", result: '{"output":"finally cancels workers"}' }),
    event(9, "subagent.finished", { status: "limit_reached", output: "Confirmed cleanup; timeout remains unchecked." }),
  ]} onClose={onClose} />);
  fireEvent.click(screen.getByText("read_file · completed"));
  expect(screen.getByText('{"path":"agent.py"}')).toBeVisible();
  expect(screen.getByText('{"output":"finally cancels workers"}')).toBeVisible();
  expect(screen.getByRole("heading", { name: "Partial report" })).toBeVisible();
  expect(screen.getByText("Confirmed cleanup; timeout remains unchecked.")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Back to conversation" }));
  expect(onClose).toHaveBeenCalled();
});
it("supports old traces without invented task or result details", () => {
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  render(<SubagentView runId="child" events={[event(1, "run.completed", {})]} onClose={() => {}} />);
  expect(screen.getByText("Task details were not recorded for this run.")).toBeVisible();
});
it("keeps the child reader's scroll position while more output arrives", () => {
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  const initial = [event(1, "model.delta", { text: "Initial output" })];
  const view = render(<SubagentView runId="child" events={initial} onClose={() => {}} />);
  const node = screen.getByLabelText("Subagent execution");
  Object.defineProperties(node, { scrollHeight: { value: 1200 }, clientHeight: { value: 400 } });
  fireEvent.wheel(node, { deltaY: -120 });
  node.scrollTop = 100;
  fireEvent.scroll(node);
  view.rerender(<SubagentView runId="child" events={[...initial, event(2, "model.delta", { text: " continuing" })]} onClose={() => {}} />);
  expect(node.scrollTop).toBe(100);
  fireEvent.click(screen.getByRole("button", { name: "Jump to latest" }));
  expect(node.scrollTop).toBe(800);
});
