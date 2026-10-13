import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { McpConfig, McpServer, McpToolInfo, McpAuthorization } from "../types";

const empty = (): McpConfig => ({ transport: "stdio", enabled: false, command: "", args: [], url: null,
  env: {}, headers: {}, timeout_seconds: 30, read_only_tools: [], subagent_tools: [] });
function references(value: string): Record<string, string> {
  const result: Record<string, string> = {};
  for (const line of value.split("\n").filter(v => v.trim())) {
    const index = line.indexOf("=");
    if (index < 1) throw new Error("Use NAME=ENVIRONMENT_VARIABLE for each reference.");
    result[line.slice(0, index).trim()] = line.slice(index + 1).trim();
  }
  return result;
}
const referenceText = (value: Record<string, string>) => Object.entries(value).map(([k, v]) => `${k}=${v}`).join("\n");

export function McpPanel({ workspace, onClose }: { workspace: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [servers, setServers] = useState<McpServer[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [id, setId] = useState("");
  const [draft, setDraft] = useState<McpConfig>(empty);
  const [args, setArgs] = useState("");
  const [refs, setRefs] = useState("");
  const [tools, setTools] = useState<McpToolInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [authorization, setAuthorization] = useState<McpAuthorization>({ status: "not_connected" });
  useEffect(() => {
    if (!selected || authorization.status !== "authorizing") return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api.mcpAuthorization(selected!);
        if (stopped) return;
        if (result.status === "connected") {
          const tested = await api.testMcp(selected!, workspace);
          if (stopped) return;
          setTools(tested.tools); await load();
          if (tested.status !== "ok") setError(tested.error ?? "Connection test failed.");
          else setNotice("Account connected. Enable this connection for new runs.");
          setAuthorization(result);
        } else {
          setAuthorization(result);
          if (result.status === "failed") setError(result.error ?? "Authorization failed.");
          else if (result.status === "authorizing") timer = setTimeout(() => void poll(), 1000);
        }
      } catch (e) { if (!stopped) { setError(String(e)); timer = setTimeout(() => void poll(), 2000); } }
    }
    timer = setTimeout(() => void poll(), 500);
    return () => { stopped = true; clearTimeout(timer); };
  }, [selected, authorization.status, workspace]);
  async function load() { const data = await api.mcpServers(); setServers(data.servers); }
  useEffect(() => {
    dialog.current?.showModal();
    void load().catch(e => setError(String(e)));
  }, []);
  function choose(server: McpServer | null) {
    const next = server ?? empty();
    setSelected(server?.id ?? null); setId(server?.id ?? "");
    setDraft({ transport: next.transport, enabled: next.enabled, command: next.command, args: next.args,
      url: next.url, proxy_env: next.proxy_env ?? null, env: next.env, headers: next.headers, timeout_seconds: next.timeout_seconds,
      read_only_tools: next.read_only_tools, subagent_tools: next.subagent_tools });
    setDraft(current => ({ ...current, auth_mode: next.auth_mode ?? "none", oauth_client_id: next.oauth_client_id,
      oauth_client_secret_env: next.oauth_client_secret_env, oauth_issuer: next.oauth_issuer,
      oauth_redirect_port: next.oauth_redirect_port ?? 0, oauth_client_metadata_url: next.oauth_client_metadata_url }));
    setAuthorization(server?.authorization ?? { status: "not_connected" });
    setArgs(next.args.join("\n")); setRefs(referenceText(next.transport === "stdio" ? next.env : next.headers));
    setTools(server?.tools ?? []); setError(""); setNotice("");
  }
  async function action(work: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await work(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function save() {
    const mapped = references(refs);
    const config = { ...draft, command: draft.transport === "stdio" ? draft.command : null,
      proxy_env: draft.transport === "streamable_http" ? draft.proxy_env ?? null : null,
      url: draft.transport === "streamable_http" ? draft.url : null,
      args: draft.transport === "stdio" ? args.split("\n").filter(Boolean) : [],
      auth_mode: draft.transport === "stdio" ? "none" as const : draft.auth_mode ?? "none",
      env: draft.transport === "stdio" ? mapped : {}, headers: draft.transport === "streamable_http" ? mapped : {} };
    await api.saveMcp(id, config); setSelected(id); await load(); setNotice("Saved. New runs use this configuration.");
  }
  function permission(name: string, field: "read_only_tools" | "subagent_tools", checked: boolean) {
    setDraft(current => {
      const values = checked ? [...new Set([...current[field], name])] : current[field].filter(v => v !== name);
      return { ...current, [field]: values,
        ...(field === "read_only_tools" && !checked ? { subagent_tools: current.subagent_tools.filter(v => v !== name) } : {}) };
    });
  }
  const signingIn = authorization.status === "authorizing";
  const statusText: Record<string, string> = {
    not_connected: "Not connected", authorizing: "Waiting for sign-in", connected: "Connected",
    needs_authorization: "Sign-in required", failed: "Connection failed", cancelled: "Sign-in cancelled",
    not_required: "No sign-in needed", unavailable: "Sign-in unavailable",
  };
  return <dialog ref={dialog} className="mcp-dialog" aria-label="MCP connections" onCancel={onClose}>
    <header className="mcp-heading"><h2>MCP connections</h2><button className="mcp-quiet" onClick={onClose}>Close</button></header>
    <div className="mcp-layout">
      <nav className="mcp-list" aria-label="MCP servers">
        <button className="mcp-add" disabled={busy} onClick={() => choose(null)}>+ Add connection</button>
        {servers.length === 0 && <p className="mcp-hint">Add a server to get started.</p>}
        {servers.map(server => <button key={server.id} disabled={busy} aria-pressed={selected === server.id} onClick={() => choose(server)}>
          <strong>{server.id}</strong><small>{server.enabled ? "Enabled" : "Disabled"}</small>
        </button>)}
      </nav>
      <form className="mcp-form" onSubmit={event => { event.preventDefault(); void action(save); }}>
        <div className="mcp-content">
          <section className="mcp-section" aria-label="Connection settings">
            <h3>{selected ? "Connection" : "New connection"}</h3>
            <div className="mcp-field-pair">
              <label>Name<input required value={id} disabled={busy || selected !== null} onChange={e => setId(e.target.value)} pattern="[A-Za-z0-9_-]{1,32}" placeholder="e.g. github" /></label>
              <label>Connection type<select disabled={busy || signingIn} value={draft.transport} onChange={e => {
                setDraft({ ...draft, transport: e.target.value as McpConfig["transport"], auth_mode: e.target.value === "streamable_http" ? "oauth" : "none" }); setRefs(""); setTools([]);
              }}><option value="stdio">Local process</option><option value="streamable_http">HTTP</option></select></label>
            </div>
            {draft.transport === "stdio" ? <>
              <label>Command<input required disabled={busy || signingIn} value={draft.command ?? ""} onChange={e => setDraft({ ...draft, command: e.target.value })} placeholder="Executable path" /></label>
              <label>Arguments, one per line<textarea disabled={busy} value={args} onChange={e => setArgs(e.target.value)} rows={3} /></label>
            </> : <label>Server URL<input type="url" required disabled={busy || signingIn} value={draft.url ?? ""} onChange={e => setDraft({ ...draft, url: e.target.value })} placeholder="https://example.com/mcp" /></label>}
            {draft.transport === "streamable_http" && <label>Authorization<select disabled={busy || signingIn} value={draft.auth_mode ?? "none"} onChange={e => {
              setDraft({ ...draft, auth_mode: e.target.value as "none" | "oauth" }); setRefs("");
            }}><option value="oauth">Browser sign-in</option><option value="none">None / manual headers</option></select></label>}
          </section>

          <section className="mcp-section" aria-label="Credentials">
            <h3>Credentials</h3>
            {draft.transport === "streamable_http" && draft.auth_mode === "oauth" ? <>
              <details className="mcp-disclosure" open={!!draft.oauth_client_id}>
                <summary>Registered application</summary>
                <div className="mcp-disclosure-body">
                  <p className="mcp-hint">Only needed when the service requires your own application.</p>
                  <label>OAuth client ID<input disabled={busy || signingIn} value={draft.oauth_client_id ?? ""} onChange={e => setDraft({ ...draft, oauth_client_id: e.target.value || null })} /></label>
                  <label>OAuth issuer<input disabled={busy || signingIn} type="url" value={draft.oauth_issuer ?? ""} onChange={e => setDraft({ ...draft, oauth_issuer: e.target.value || null })} /></label>
                  <div className="mcp-field-pair">
                    <label>Client secret environment variable<input disabled={busy || signingIn} pattern="[A-Za-z_][A-Za-z0-9_]*" value={draft.oauth_client_secret_env ?? ""} onChange={e => setDraft({ ...draft, oauth_client_secret_env: e.target.value || null })} placeholder="GITHUB_OAUTH_CLIENT_SECRET" aria-describedby="mcp-secret-hint" /></label>
                    <label>Callback port (0 for automatic)<input disabled={busy || signingIn} type="number" min={0} max={65535} value={draft.oauth_redirect_port ?? 0} onChange={e => setDraft({ ...draft, oauth_redirect_port: Number(e.target.value) })} /></label>
                  </div>
                  <p id="mcp-secret-hint" className="mcp-hint">Enter the variable name, not the secret.</p>
                </div>
              </details>
            </> : <>
              <label>{draft.transport === "stdio" ? "Environment references" : "Header references"}<textarea disabled={busy} rows={2} value={refs} onChange={e => setRefs(e.target.value)} placeholder={draft.transport === "stdio" ? "API_KEY=MY_SERVICE_KEY" : "Authorization=MY_AUTH_HEADER"} /></label>
              <p className="mcp-hint">Use NAME=VARIABLE. Values stay in your environment.</p>
            </>}
            {draft.transport === "streamable_http" && <label>Proxy environment variable<input disabled={busy || signingIn} pattern="[A-Za-z_][A-Za-z0-9_]*" value={draft.proxy_env ?? ""} onChange={e => setDraft({ ...draft, proxy_env: e.target.value || null })} placeholder="Optional, e.g. HTTPS_PROXY" /></label>}
            <details className="mcp-disclosure">
              <summary>Advanced settings</summary>
              <div className="mcp-disclosure-body">
                <label>Timeout in seconds<input type="number" min={1} max={300} value={draft.timeout_seconds} disabled={busy || signingIn} onChange={e => setDraft({ ...draft, timeout_seconds: Number(e.target.value) })} /></label>
                {draft.transport === "streamable_http" && draft.auth_mode === "oauth" && <>
                  <label>Client metadata document URL<input disabled={busy || signingIn} type="url" value={draft.oauth_client_metadata_url ?? ""} onChange={e => setDraft({ ...draft, oauth_client_metadata_url: e.target.value || null })} /></label>
                  <label>Header references<textarea disabled={busy || signingIn} rows={2} value={refs} onChange={e => setRefs(e.target.value)} placeholder="HEADER_NAME=VARIABLE" /></label>
                </>}
              </div>
            </details>
            {draft.transport === "streamable_http" && draft.auth_mode === "oauth" && <section className="mcp-account" aria-label="Account authorization">
              <div className="mcp-account-copy"><strong>Account</strong><span className={`mcp-account-state${authorization.status === "connected" ? " is-connected" : ""}`} role="status">{statusText[authorization.status] ?? authorization.status}</span></div>
              <div className="mcp-account-actions">
                {signingIn ? <>
                  {authorization.authorization_url && <a href={authorization.authorization_url} target="_blank" rel="noreferrer">Open authorization page</a>}
                  <button type="button" onClick={() => void action(async () => { setAuthorization(await api.cancelMcpAuthorization(selected!)); })}>Cancel authorization</button>
                </> : <button className="mcp-primary" type="button" disabled={busy || !id || !workspace} onClick={event => {
                  if (!event.currentTarget.form?.reportValidity()) return;
                  void action(async () => { await save(); setAuthorization(await api.authorizeMcp(id, workspace)); });
                }}>{authorization.status === "connected" ? "Reconnect account" : "Connect account"}</button>}
                {authorization.status === "connected" && <button className="mcp-quiet" type="button" disabled={busy} onClick={() => void action(async () => {
                  setAuthorization(await api.disconnectMcp(selected!)); setTools([]); await load();
                })}>Disconnect account</button>}
              </div>
            </section>}
          </section>

          {!!tools.length && <section className="mcp-section mcp-tools" aria-label="MCP tools">
            <h3>Tools <span>{tools.length}</span></h3>
            {tools.map(tool => <details className="mcp-tool" key={tool.name}>
              <summary><span><b>{tool.name}</b><span className="mcp-tool-description">{tool.description}</span></span></summary>
              <div className="mcp-tool-body">
                <div className="mcp-tool-permissions">
                  <label className="mcp-check"><input type="checkbox" disabled={busy} checked={draft.read_only_tools.includes(tool.name)} onChange={e => permission(tool.name, "read_only_tools", e.target.checked)} />Treat as read-only</label>
                  <label className="mcp-check"><input type="checkbox" disabled={busy || !draft.read_only_tools.includes(tool.name)} checked={draft.subagent_tools.includes(tool.name)} onChange={e => permission(tool.name, "subagent_tools", e.target.checked)} />Allow subagents</label>
                </div>
                <details className="mcp-disclosure"><summary>Parameters</summary><pre>{JSON.stringify(tool.input_schema, null, 2)}</pre></details>
              </div>
            </details>)}
          </section>}
        </div>
        <footer className="mcp-footer">
          {error && <p role="alert" className="mcp-feedback mcp-error">{error}</p>}
          {notice && <p role="status" className="mcp-feedback">{notice}</p>}
          <label className="mcp-check"><input type="checkbox" disabled={busy || signingIn} checked={draft.enabled} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} />Enable for new runs</label>
          <div className="mcp-actions">
            {selected && <button className="mcp-remove" type="button" disabled={busy || signingIn} onClick={() => {
              if (window.confirm(`Remove MCP connection ${selected}?`)) void action(async () => { await api.deleteMcp(selected); choose(null); await load(); });
            }}>Remove</button>}
            <div>
              {selected && <button disabled={busy || signingIn || !workspace} type="button" onClick={() => void action(async () => {
                const result = await api.testMcp(selected, workspace); setTools(result.tools); await load();
                if (result.status !== "ok") throw new Error(result.error ?? "Connection test failed.");
                setNotice(`Test passed. ${result.tools.length} tools available.`);
              })}>{busy ? "Working…" : "Test saved connection"}</button>}
              <button className="mcp-primary" disabled={busy || signingIn} type="submit">Save</button>
            </div>
          </div>
        </footer>
      </form>
    </div>
  </dialog>;
}
