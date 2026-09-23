import { useState, type FormEventHandler } from "react";
import { isTauri } from "@tauri-apps/api/core";
import type { Session } from "../types";

interface Props {
  sessions: Session[];
  selected: Session | null;
  workspace: string;
  onWorkspaceChange: (value: string) => void;
  onBrowse: () => void;
  onCreate: FormEventHandler<HTMLFormElement>;
  onSelect: (session: Session) => void;
  onDelete: (session: Session) => Promise<boolean>;
}

export function SessionSidebar({
  sessions, selected, workspace, onWorkspaceChange, onBrowse, onCreate, onSelect, onDelete,
}: Props) {
  const desktop = isTauri();
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function confirmDelete(session: Session) {
    setDeleting(true);
    try {
      if (await onDelete(session)) setConfirmId(null);
    } finally {
      setDeleting(false);
    }
  }
  return (
    <aside className="sessions-panel panel">
      <div className="panel-heading">
        <div><span className="section-note">Local workspaces</span><strong>Recent sessions</strong></div>
        <b>{sessions.length}</b>
      </div>
      <form className="new-session" onSubmit={onCreate}>
        <label htmlFor="workspace">Open a workspace</label>
        <div className="workspace-picker">
          <input
            id="workspace"
            value={workspace}
            onChange={(event) => onWorkspaceChange(event.target.value)}
            onClick={desktop ? onBrowse : undefined}
            readOnly={desktop}
            placeholder="/path/to/project"
            title={desktop ? "Choose a workspace folder" : undefined}
          />
          {desktop && <button type="button" onClick={onBrowse} aria-label="Browse workspace folders">Browse…</button>}
        </div>
        <button type="submit" aria-label="New session"><span>＋</span> New session</button>
      </form>
      <div className="session-list">
        {sessions.map((session) => (
          <div className="session-item" key={session.session_id}>
            <div className="session-row">
              <button
                className={`session-open ${selected?.session_id === session.session_id ? "selected" : ""}`}
                onClick={() => onSelect(session)}
              >
                <span>{session.workspace.split("/").filter(Boolean).at(-1) || "/"}</span>
                <small>{session.message_count} messages · {session.approval_mode}</small>
              </button>
              <button
                className="session-remove"
                type="button"
                aria-label={`Delete session ${session.session_id.slice(0, 8)}`}
                title="Delete session"
                onClick={() => setConfirmId(session.session_id)}
              >×</button>
            </div>
            {confirmId === session.session_id && (
              <div className="session-delete-confirm" role="group" aria-label="Confirm delete session">
                <p>Delete this session and its history? This cannot be undone.</p>
                <div>
                  <button type="button" disabled={deleting} onClick={() => setConfirmId(null)}>Cancel</button>
                  <button type="button" disabled={deleting} onClick={() => void confirmDelete(session)}>
                    {deleting ? "Deleting…" : "Delete session"}
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
        {!sessions.length && <p className="empty-note">Create a session to begin.</p>}
      </div>
    </aside>
  );
}
