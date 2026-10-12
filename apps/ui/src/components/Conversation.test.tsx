import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Conversation } from "./Conversation";
import type { Session } from "../types";

afterEach(cleanup);
const session: Session = { session_id: "one", workspace: "/project", model: null, approval_mode: "ask", created_at: "", updated_at: "", message_count: 0 };
const props = { selected: session, messages: [], liveText: "First", liveReasoning: "", models: [], activeRun: null,
  approval: null, prompt: "", onPromptChange: vi.fn(), onSubmit: vi.fn(), onStop: vi.fn(), onApproval: vi.fn(), onModeChange: vi.fn(), onModelChange: vi.fn() };
function geometry(node: HTMLElement) {
  Object.defineProperties(node, { scrollHeight: { value: 1200, configurable: true }, clientHeight: { value: 400, configurable: true } });
}
it("follows streaming until the user scrolls up, then resumes at the bottom", () => {
  const view = render(<Conversation {...props} />);
  const node = screen.getByLabelText("Conversation messages");
  geometry(node);
  view.rerender(<Conversation {...props} liveText="Second" />);
  expect(node.scrollTop).toBe(800);
  fireEvent.wheel(node, { deltaY: -120 });
  node.scrollTop = 200;
  fireEvent.scroll(node);
  view.rerender(<Conversation {...props} liveText="Still growing" liveReasoning="More reasoning" />);
  expect(node.scrollTop).toBe(200);
  expect(screen.getByRole("button", { name: "Jump to latest" })).toBeVisible();
  node.scrollTop = 800;
  fireEvent.scroll(node);
  Object.defineProperty(node, "scrollHeight", { value: 1400, configurable: true });
  view.rerender(<Conversation {...props} liveText="Newest" />);
  expect(node.scrollTop).toBe(1000);
  expect(screen.queryByRole("button", { name: "Jump to latest" })).toBeNull();
});
it("can jump back to latest and resets following when switching sessions", () => {
  const view = render(<Conversation {...props} />);
  const node = screen.getByLabelText("Conversation messages");
  geometry(node);
  fireEvent.wheel(node, { deltaY: -120 });
  node.scrollTop = 100;
  fireEvent.scroll(node);
  fireEvent.click(screen.getByRole("button", { name: "Jump to latest" }));
  expect(node.scrollTop).toBe(800);
  fireEvent.wheel(node, { deltaY: -120 });
  node.scrollTop = 100;
  fireEvent.scroll(node);
  view.rerender(<Conversation {...props} selected={{ ...session, session_id: "two" }} />);
  expect(node.scrollTop).toBe(800);
});
