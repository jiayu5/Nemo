import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { MemoryDraft, MemoryEntry, MemoryState, Message, Session } from "../types";

const emptyDraft: MemoryDraft = { scope: "project", title: "", content: "", kind: "project" };

export function MemoryPanel({ session, onClose }: { session: Session | null; onClose: () => void }) {
  const [state, setState] = useState<MemoryState | null>(null);
  const [draft, setDraft] = useState<MemoryDraft>(emptyDraft);
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

  return <dialog className="memory-dialog" ref={dialog} onCancel={onClose} aria-label="Saved memories">
    <header><h2>Saved memories</h2><button type="button" onClick={onClose}>Close</button></header>
    {!session ? <p>Select a session to manage its project memories.</p> : <>
      <p>User memories apply across projects. Project memories belong to {session.workspace}.</p>
      {error && <p role="alert" className="error-banner">{error}</p>}
      {state && <label><input type="checkbox" checked={state.enabled} disabled={busy}
        onChange={event => void act(() => api.setMemoryEnabled(event.target.checked))} />
        Enable memory recall and agent saving</label>}
      <p>Changes are announced on the next turn. Turning memory off does not erase earlier chat messages.</p>
      <button type="button" disabled={busy} onClick={() => void act(async () => {})}>Refresh</button>
      <div className="memory-list">{state?.entries.map(entry => <article key={`${entry.scope}/${entry.id}`}>
        <h3>{entry.title} <small>{entry.scope}</small></h3>
        <p className="memory-content">{entry.content}</p>
        <small>{entry.kind} · {new Date(entry.updated_at).toLocaleString()}</small>
        <div className="memory-actions">
          <button type="button" disabled={busy} onClick={() => { setDraft({ scope: entry.scope, title: entry.title,
            content: entry.content, kind: entry.kind, id: entry.id, revision: entry.revision }); setForget(null); }}>Edit</button>
          <button type="button" disabled={busy} onClick={() => setForget(entry)}>Forget</button>
          {entry.source_session_id ? <button type="button" disabled={busy} onClick={() => void act(async () => {
            const messages = await api.messages(entry.source_session_id!);
            setSource({ id: entry.source_session_id!, messages, position: entry.source_message_index });
          })}>View source</button> : <span>Manually saved</span>}
        </div>
        {forget?.id === entry.id && <div>
          <p>Remove this from future recall? Earlier chats and a local retired copy will remain.</p>
          <button type="button" disabled={busy} onClick={() => void act(async () => {
            await api.forgetMemory(session.session_id, entry); setForget(null);
            if (draft.id === entry.id) setDraft(emptyDraft);
          })}>Confirm forget</button>
          <button type="button" onClick={() => setForget(null)}>Cancel</button>
        </div>}
      </article>)}</div>
      {state?.entries.length === 0 && <p>No saved memories yet.</p>}
      {source && <section><h3>Source session {source.id}</h3>
        <p>Source message: {source.position === null ? "unknown" : source.position + 1}</p>
        {source.messages.slice(Math.max(0, (source.position ?? 0) - 1), (source.position ?? 0) + 3).map((m, i) =>
          <p className="memory-content" key={i}><b>{m.role}: </b>{m.content || JSON.stringify(m.tool_result)}</p>)}
        <button type="button" onClick={() => setSource(null)}>Hide source</button></section>}
      <form onSubmit={event => { event.preventDefault(); void act(async () => {
        await api.saveMemory(session.session_id, draft); setDraft(emptyDraft);
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
          {draft.id && <button type="button" disabled={busy} onClick={() => setDraft(emptyDraft)}>Cancel edit</button>}</div>
      </form>
    </>}
  </dialog>;
}
