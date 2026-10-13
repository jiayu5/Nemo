import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { InspectorPanel } from "./InspectorPanel";
import type { RunEvent } from "../types";

afterEach(cleanup);
const event = (seq: number, id: string, type: string, status?: string): RunEvent => ({
  run_id: "parent", seq, step: 2, type: "subagent.event", timestamp: "",
  payload: { child_run_id: id, child_step: 1, child_type: type, child_payload: { status } },
});
it("shows separate concurrent child states and updates them from replayed events", () => {
  const initial = [event(1, "aaaaaaa", "run.started"), event(2, "bbbbbbb", "subagent.queued")];
  const select = vi.fn();
  const props = { runs: [], trace: null, onSelectRun: () => {}, onSelectSubagent: select };
  const view = render(<InspectorPanel {...props} events={initial} />);
  let children = within(screen.getByRole("region", { name: "Subagents" }));
  expect(children.getByText("running · step 1")).toBeVisible();
  expect(children.getByText("queued · step 1")).toBeVisible();
  fireEvent.click(children.getByRole("button", { name: "View subagent aaaaaaa" }));
  expect(select).toHaveBeenCalledWith("aaaaaaa");
  view.rerender(<InspectorPanel {...props} events={[...initial,
    event(3, "aaaaaaa", "subagent.finished", "completed"),
    event(4, "bbbbbbb", "subagent.finished", "cancelled"),
    event(5, "aaaaaaa", "model.delta"),
  ]} />);
  children = within(screen.getByRole("region", { name: "Subagents" }));
  expect(children.getByText("completed · step 1")).toBeVisible();
  expect(children.getByText("cancelled · step 1")).toBeVisible();
  expect(screen.queryByText(/model.delta/)).toBeNull();
});

it("shows a resumed child queued with its cumulative step count", () => {
  const resumed = event(2, "aaaaaaa", "subagent.resumed");
  resumed.payload.child_step = 20;
  resumed.payload.child_payload = { round: 2 };
  render(<InspectorPanel runs={[]} trace={null} onSelectRun={() => {}} events={[
    event(1, "aaaaaaa", "subagent.finished", "limit_reached"), resumed,
  ]} />);
  expect(screen.getByText("queued · step 20 · round 2")).toBeVisible();
});

it("names a skipped MCP connection and its reason", () => {
  const skipped = (seq: number, serverId: string | null): RunEvent => ({
    run_id: "parent", seq, step: 0, type: "mcp.connection_skipped", timestamp: "",
    payload: { server_id: serverId, reason: "MCP account needs authorization; connect it in MCP settings" },
  });
  render(<InspectorPanel runs={[]} trace={null} onSelectRun={() => {}} events={[
    skipped(1, "Github"), skipped(2, null),
  ]} />);
  expect(screen.getByText(/MCP Github not loaded/)).toBeVisible();
  expect(screen.getByText(/MCP tools not loaded/)).toBeVisible();
  expect(screen.getAllByText(/connect it in MCP settings/)).toHaveLength(2);
});
