import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { MemoryDraft, MemoryEntry, MemoryState, Message, Session } from "../types";

const emptyDraft: MemoryDraft = { scope: "project", title: "", content: "", kind: "project" };

export function MemoryPanel({ session, onClose }: { session: Session | null; onClose: () => void }) {
  const [state, setState] = useState<MemoryState | null>(null);
  const [draft, setDraft] = useState<MemoryDraft>(emptyDraft);
  const [editing, setEditing] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selected = state?.entries.find(entry => `${entry.scope}/${entry.id}` === selectedKey);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [forget, setForget] = useState<MemoryEntry | null>(null);
  const [source, setSource] = useState<{ id: string; messages: Message[]; position: number | null } | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    let active = true;
    if (session) api.memories(session.session_id).then(value => { if (active) setState(value); })
      .catch(reason => { if (active) setError(String(reason)); });
    return () => { active = false; };
  }, [session]);

  async function act(action: () => Promise<unknown>) {
    if (!session) return;
    setBusy(true); setError("");
    try {
      await action();
      setState(await api.memories(session.session_id));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not update memory"); }
    finally { setBusy(false); }
  }

  return <dialog className="memory-dialog skills-dialog memories-dialog" ref={dialog} onCancel={onClose} aria-label="Saved memories">
    <header><h2>Saved memories</h2><button type="button" onClick={onClose}>Close</button></header>
    {!session ? <p>Select a session to manage its project memories.</p> : <>
      {error && <p role="alert" className="error-banner">{error}</p>}
      <div className="memory-toolbar">
        {state && <label title="Enable agent recall and saving. Turning this off keeps earlier chat messages."><input type="checkbox" checked={state.enabled} disabled={busy}
          onChange={event => void act(() => api.setMemoryEnabled(event.target.checked))} /> Memory enabled</label>}
        <button type="button" disabled={busy} onClick={() => {
          setDraft(emptyDraft); setEditing(true); setSelectedKey(null); setForget(null); setSource(null);
        }}>Add memory</button>
      </div>
      {!state && !error && <p role="status">Loading memories…</p>}
      <div className="skills-list">{state?.entries.map(entry => <button className="skill-row" key={`${entry.scope}/${entry.id}`}
        type="button" disabled={busy} aria-label={`View ${entry.title}`} aria-expanded={selectedKey === `${entry.scope}/${entry.id}`}
        title={entry.scope === "user" ? "All projects" : `This project: ${session.workspace}`}
        onClick={() => { setSelectedKey(`${entry.scope}/${entry.id}`); setEditing(false); setForget(null); setSource(null); }}>
        <span className="skill-icon" aria-hidden="true"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M4 5h16v14H4zM8 9h8M8 13h6" />
        </svg></span>
        <span className="skill-copy"><span className="skill-name">{entry.title}</span>
          <span className="skill-description" title={entry.content}>{entry.content.replace(/\s+/g, " ")}</span></span>
      </button>)}</div>
      {selected && !editing && <section aria-label="Memory details">
        <h3>{selected.title}</h3>
        <p className="memory-content">{selected.content}</p>
        <small className="memory-meta">{selected.scope === "user" ? "All projects" : "This project"} · {selected.kind} · {new Date(selected.updated_at).toLocaleString()}</small>
        <div className="memory-actions">
          <button type="button" disabled={busy} onClick={() => { setDraft({ scope: selected.scope, title: selected.title,
            content: selected.content, kind: selected.kind, id: selected.id, revision: selected.revision }); setForget(null); setEditing(true); setSource(null); }}>Edit</button>
          <button type="button" disabled={busy} onClick={() => setForget(selected)}>Forget</button>
          {selected.source_session_id ? <button type="button" disabled={busy} onClick={() => void act(async () => {
            const messages = await api.messages(selected.source_session_id!);
            setSource({ id: selected.source_session_id!, messages, position: selected.source_message_index });
          })}>View source</button> : <span>Manually saved</span>}
        </div>
        {forget?.id === selected.id && <div>
          <p>Remove this from future recall? Earlier chats and a local retired copy will remain.</p>
          <button type="button" disabled={busy} onClick={() => void act(async () => {
            await api.forgetMemory(session.session_id, selected); setForget(null);
            if (draft.id === selected.id && draft.scope === selected.scope) setDraft(emptyDraft);
            setSelectedKey(null); setSource(null);
          })}>Confirm forget</button>
          <button type="button" onClick={() => setForget(null)}>Cancel</button>
        </div>}
        <button type="button" onClick={() => { setSelectedKey(null); setForget(null); setSource(null); }}>Hide details</button>
      </section>}
      {state?.entries.length === 0 && <p>No saved memories yet.</p>}
      {source && <section><h3>Source session {source.id}</h3>
        <p>Source message: {source.position === null ? "unknown" : source.position + 1}</p>
        {source.messages.slice(Math.max(0, (source.position ?? 0) - 1), (source.position ?? 0) + 3).map((m, i) =>
          <p className="memory-content" key={i}><b>{m.role}: </b>{m.content || JSON.stringify(m.tool_result)}</p>)}
        <button type="button" onClick={() => setSource(null)}>Hide source</button></section>}
      {editing && <form onSubmit={event => { event.preventDefault(); void act(async () => {
        await api.saveMemory(session.session_id, draft); setDraft(emptyDraft); setEditing(false);
      }); }}>
        <h3>{draft.id ? "Edit memory" : "Add memory"}</h3>
        <label>Scope<select aria-label="Memory scope" value={draft.scope} disabled={!!draft.id || busy}
          onChange={e => setDraft({ ...draft, scope: e.target.value as MemoryDraft["scope"] })}>
          <option value="project">This project</option><option value="user">All projects (user)</option></select></label>
        <label>Kind<select aria-label="Memory kind" value={draft.kind} disabled={busy}
          onChange={e => setDraft({ ...draft, kind: e.target.value as MemoryDraft["kind"] })}>
          <option value="project">Project background</option><option value="preference">Preference</option>
          <option value="feedback">Feedback</option><option value="reference">Reference</option></select></label>
        <label>Title<input aria-label="Memory title" required maxLength={100} value={draft.title} disabled={busy}
          onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>
        <label>Content<textarea aria-label="Memory content" required maxLength={3000} value={draft.content} disabled={busy}
          onChange={e => setDraft({ ...draft, content: e.target.value })} /></label>
        <div className="memory-actions"><button type="submit" disabled={busy}>{busy ? "Saving…" : "Save memory"}</button>
          <button type="button" disabled={busy} onClick={() => { setDraft(emptyDraft); setEditing(false); }}>Cancel</button></div>
      </form>}
    </>}
  </dialog>;
}
