import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { McpPanel } from "./McpPanel";
import { api } from "../api";
import type { McpServer } from "../types";

vi.mock("../api", () => ({ api: { mcpServers: vi.fn(), saveMcp: vi.fn(), deleteMcp: vi.fn(), testMcp: vi.fn(),
  authorizeMcp: vi.fn(), mcpAuthorization: vi.fn(), cancelMcpAuthorization: vi.fn(), disconnectMcp: vi.fn() } }));
afterEach(cleanup);
beforeEach(() => { vi.resetAllMocks(); HTMLDialogElement.prototype.showModal = function () { this.open = true; }; });
const local: McpServer = { id: "demo", transport: "stdio", enabled: false, command: "python", args: ["server.py"],
  url: null, env: {}, headers: {}, timeout_seconds: 30, read_only_tools: [], subagent_tools: [], status: "not_tested", tools: [] };
it("saves a disabled local connection with literal argument boundaries", async () => {
  vi.mocked(api.mcpServers).mockResolvedValue({ servers: [] });
  vi.mocked(api.saveMcp).mockResolvedValue({ status: "saved" });
  render(<McpPanel workspace="/workspace" onClose={() => {}} />);
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "demo" } });
  fireEvent.change(screen.getByLabelText("Command"), { target: { value: "python" } });
  fireEvent.change(screen.getByLabelText("Arguments, one per line"), { target: { value: "server.py\nargument with spaces" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.saveMcp).toHaveBeenCalledWith("demo", expect.objectContaining({ enabled: false, args: ["server.py", "argument with spaces"] })));
  expect(api.testMcp).not.toHaveBeenCalled();
});
it("tests the saved connection and requires read-only before enabling subagents", async () => {
  vi.mocked(api.mcpServers).mockResolvedValue({ servers: [local] });
  vi.mocked(api.testMcp).mockResolvedValue({ status: "ok", tools: [{ name: "add", model_name: "mcp_demo_add", description: "Add integers", input_schema: { type: "object" }, read_only: false, subagent_allowed: false }] });
  render(<McpPanel workspace="/workspace" onClose={() => {}} />);
  fireEvent.click(await screen.findByText("demo"));
  fireEvent.click(screen.getByRole("button", { name: "Test saved connection" }));
  await screen.findByText("Add integers");
  expect(api.testMcp).toHaveBeenCalledWith("demo", "/workspace");
  expect(screen.getByLabelText("Allow subagents")).toBeDisabled();
  fireEvent.click(screen.getByLabelText("Treat as read-only"));
  fireEvent.click(screen.getByLabelText("Allow subagents"));
  fireEvent.click(screen.getByLabelText("Treat as read-only"));
  expect(screen.getByLabelText("Allow subagents")).not.toBeChecked();
  expect(screen.getByLabelText("Allow subagents")).toBeDisabled();
});
it("reports HTTP discovery failure without showing invented tools", async () => {
  vi.mocked(api.mcpServers).mockResolvedValue({ servers: [{ ...local, transport: "streamable_http", command: null, args: [], url: "https://example.com/mcp" }] });
  vi.mocked(api.testMcp).mockResolvedValue({ status: "failed", tools: [], error: "Connection unavailable" });
  render(<McpPanel workspace="/workspace" onClose={() => {}} />);
  fireEvent.click(await screen.findByText("demo"));
  expect(screen.getByLabelText("Server URL")).toHaveValue("https://example.com/mcp");
  fireEvent.click(screen.getByRole("button", { name: "Test saved connection" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Connection unavailable");
  expect(screen.queryByRole("region", { name: "MCP tools" })).toBeNull();
});

it("connects an account through browser sign-in, tests it and disconnects", async () => {
  const remote: McpServer = { ...local, transport: "streamable_http", command: null, args: [],
    url: "https://oauth.test/mcp", auth_mode: "oauth", authorization: { status: "not_connected" } };
  vi.mocked(api.mcpServers).mockResolvedValue({ servers: [remote] });
  vi.mocked(api.saveMcp).mockResolvedValue({ status: "saved" });
  vi.mocked(api.authorizeMcp).mockResolvedValue({ status: "authorizing", authorization_url: "https://oauth.test/authorize?state=opaque" });
  vi.mocked(api.mcpAuthorization).mockResolvedValue({ status: "connected" });
  vi.mocked(api.testMcp).mockResolvedValue({ status: "ok", tools: [{ name: "add", model_name: "mcp_demo_add",
    description: "Add integers", input_schema: { type: "object" }, read_only: false, subagent_allowed: false }] });
  vi.mocked(api.disconnectMcp).mockResolvedValue({ status: "not_connected" });
  render(<McpPanel workspace="/workspace" onClose={() => {}} />);
  fireEvent.click(await screen.findByText("demo"));
  fireEvent.click(screen.getByRole("button", { name: "Connect account" }));
  await waitFor(() => expect(api.authorizeMcp).toHaveBeenCalledWith("demo", "/workspace"), { timeout: 3000 });
  // The user keeps the same page open while the browser installs the authorization.
  expect(await screen.findByRole("link", { name: "Open authorization page" }))
    .toHaveAttribute("href", "https://oauth.test/authorize?state=opaque");
  await waitFor(() => expect(api.testMcp).toHaveBeenCalledWith("demo", "/workspace"), { timeout: 5000 });
  await screen.findByText("Add integers");
  fireEvent.click(screen.getByRole("button", { name: "Disconnect account" }));
  await waitFor(() => expect(api.disconnectMcp).toHaveBeenCalledWith("demo"));
});

it("keeps the discovery result when authorization finishes before a slow test", async () => {
  const remote: McpServer = { ...local, transport: "streamable_http", command: null, args: [],
    url: "https://oauth.test/mcp", auth_mode: "oauth", authorization: { status: "authorizing" } };
  vi.mocked(api.mcpServers).mockResolvedValue({ servers: [remote] });
  vi.mocked(api.mcpAuthorization).mockResolvedValue({ status: "connected" });
  let finish!: (result: Awaited<ReturnType<typeof api.testMcp>>) => void;
  vi.mocked(api.testMcp).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  render(<McpPanel workspace="/workspace" onClose={() => {}} />);
  fireEvent.click(await screen.findByText("demo"));
  await waitFor(() => expect(api.testMcp).toHaveBeenCalled(), { timeout: 3000 });
  await new Promise(resolve => setTimeout(resolve, 20));
  finish({ status: "ok", tools: [{ name: "add", model_name: "mcp_demo_add", description: "Discovered after sign-in",
    input_schema: { type: "object" }, read_only: false, subagent_allowed: false }] });
  expect(await screen.findByText("Discovered after sign-in")).toBeInTheDocument();
  expect(screen.getByText("Account connected. Enable this connection for new runs.")).toBeInTheDocument();
});

it("validates credential variable names before saving and opening sign-in", async () => {
  const remote: McpServer = { ...local, transport: "streamable_http", command: null, args: [],
    url: "https://oauth.test/mcp", auth_mode: "oauth", oauth_client_id: "registered-client",
    oauth_issuer: "https://oauth.test", authorization: { status: "not_connected" } };
  vi.mocked(api.mcpServers).mockResolvedValue({ servers: [remote] });
  vi.mocked(api.saveMcp).mockResolvedValue({ status: "saved" });
  vi.mocked(api.authorizeMcp).mockResolvedValue({ status: "cancelled" });
  render(<McpPanel workspace="/workspace" onClose={() => {}} />);
  fireEvent.click(await screen.findByText("demo"));
  fireEvent.change(screen.getByLabelText("Client secret environment variable"), { target: { value: "Bearer invalid-value" } });
  fireEvent.click(screen.getByRole("button", { name: "Connect account" }));
  expect(api.saveMcp).not.toHaveBeenCalled();
  expect(api.authorizeMcp).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Client secret environment variable"), { target: { value: "GITHUB_OAUTH_CLIENT_SECRET" } });
  fireEvent.click(screen.getByRole("button", { name: "Connect account" }));
  await waitFor(() => expect(api.authorizeMcp).toHaveBeenCalledWith("demo", "/workspace"));
});
