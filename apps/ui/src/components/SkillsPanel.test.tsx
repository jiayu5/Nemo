import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SkillsPanel } from "./SkillsPanel";
import { api } from "../api";
import type { Session, SkillDetail } from "../types";

vi.mock("../api", () => ({ api: { skills: vi.fn(), skillDetail: vi.fn() } }));
const session: Session = { session_id: "a", workspace: "/project/long/private/workspace", model: null,
  approval_mode: "ask", created_at: "", updated_at: "", message_count: 0 };
const entry: SkillDetail = { id: "user:review", name: "review", scope: "user",
  description: "Review Python", body: "Inspect cancellation." };

describe("Skills manager", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    HTMLDialogElement.prototype.showModal = function () { this.open = true; };
    vi.mocked(api.skills).mockResolvedValue({ entries: [entry], diagnostics: [] });
    vi.mocked(api.skillDetail).mockResolvedValue(entry);
  });
  afterEach(cleanup);

  it("lists user skills without a session and reads details", async () => {
    render(<SkillsPanel session={null} onClose={() => {}} />);
    expect(await screen.findByText("review")).toBeVisible();
    expect(api.skills).toHaveBeenCalledWith(null);
    fireEvent.click(screen.getByRole("button", { name: "View review" }));
    expect(await screen.findByText("Inspect cancellation.")).toBeVisible();
    expect(api.skillDetail).toHaveBeenCalledWith("user:review", null);
  });

  it("shows and reads built-in skills without a session", async () => {
    const creator: SkillDetail = { id: "builtin:skill-creator", name: "skill-creator",
      description: "Create reusable skills", scope: "builtin", body: "Create a complete project skill." };
    vi.mocked(api.skills).mockResolvedValue({ entries: [creator], diagnostics: [] });
    vi.mocked(api.skillDetail).mockResolvedValue(creator);
    render(<SkillsPanel session={null} onClose={() => {}} />);
    expect(await screen.findByRole("button", { name: "View skill-creator" })).toHaveAttribute("title", "Built-in");
    fireEvent.click(screen.getByRole("button", { name: "View skill-creator" }));
    expect(await screen.findByText(creator.body)).toBeVisible();
    expect(api.skillDetail).toHaveBeenCalledWith(creator.id, null);
  });

  it("keeps the project path in a tooltip and has no single-file import entry", async () => {
    vi.mocked(api.skills).mockResolvedValue({ entries: [{ ...entry, id: "project:review", scope: "project" }], diagnostics: [] });
    render(<SkillsPanel session={session} onClose={() => {}} />);
    await screen.findByText("review");
    expect(screen.getByRole("button", { name: "View review" })).toHaveAttribute("title", `This project: ${session.workspace}`);
    expect(screen.queryByText(session.workspace)).toBeNull();
    expect(screen.queryByText("Import a skill")).toBeNull();
    expect(screen.queryByLabelText("Skill Markdown document")).toBeNull();
  });

  it("scans on each opening without a refresh button and surfaces errors", async () => {
    vi.mocked(api.skills).mockResolvedValueOnce({ entries: [], diagnostics: ["project: duplicate skill name"] });
    const first = render(<SkillsPanel session={session} onClose={() => {}} />);
    expect(await screen.findByText("No skills installed yet.")).toBeVisible();
    expect(screen.getByText("project: duplicate skill name")).toBeVisible();
    expect(screen.queryByRole("button", { name: /Refresh/ })).toBeNull();
    first.unmount();
    const second = render(<SkillsPanel session={session} onClose={() => {}} />);
    expect(await screen.findByText("review")).toBeVisible();
    expect(api.skills).toHaveBeenCalledTimes(2);
    second.unmount();
    vi.mocked(api.skills).mockRejectedValueOnce(new Error("Could not read skills"));
    render(<SkillsPanel session={session} onClose={() => {}} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not read skills");
  });
});
