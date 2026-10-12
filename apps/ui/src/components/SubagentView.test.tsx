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

it("replays the full report from chunks instead of the finished preview", () => {
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  render(<SubagentView runId="child" events={[
    event(1, "subagent.report", { offset: 0, text: "Full report beginning. " }),
    event(2, "subagent.report", { offset: 23, text: "Previously missing tail." }),
    event(3, "subagent.finished", { status: "completed", output: "Truncated preview" }),
  ]} onClose={() => {}} />);
  expect(screen.getByText("Full report beginning. Previously missing tail.")).toBeVisible();
  expect(screen.queryByText("Truncated preview")).toBeNull();
});

it("keeps earlier reports and separates continuation steps during live updates", () => {
  HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  const initial = [event(1, "subagent.queued", { task: "Inspect", round: 1 }),
    event(2, "subagent.report", { offset: 0, text: "Original findings", round: 1 }),
    event(3, "subagent.finished", { status: "limit_reached", output: "Original preview", round: 1 })];
  const view = render(<SubagentView runId="child" events={initial} onClose={() => {}} />);
  const resumed = [...initial, event(4, "subagent.resumed", { prompt: "Check the remaining cases", round: 2 })];
  view.rerender(<SubagentView runId="child" events={resumed} onClose={() => {}} />);
  expect(screen.getByText("queued · read-only · round 2")).toBeVisible();
  expect(screen.getByText("Original findings")).toBeVisible();
  expect(screen.getByText("Check the remaining cases")).toBeVisible();
  const delta = event(5, "model.delta", { text: "Follow-up work", round: 2 });
  delta.payload.child_step = 2;
  view.rerender(<SubagentView runId="child" events={[...resumed, delta,
    event(6, "subagent.report", { offset: 0, text: "Follow-up findings", round: 2 }),
    event(7, "subagent.finished", { status: "completed", round: 2 })]} onClose={() => {}} />);
  expect(screen.getByText("Original findings")).toBeVisible();
  expect(screen.getByText("Follow-up findings")).toBeVisible();
  expect(screen.getByText("Follow-up work")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Round 1 · limit_reached" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Round 2 · completed" })).toBeVisible();
});
