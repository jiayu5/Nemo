import type { FormEventHandler } from "react";
import type { Session } from "../types";

interface Props {
  sessions: Session[];
  selected: Session | null;
  workspace: string;
  onWorkspaceChange: (value: string) => void;
  onCreate: FormEventHandler<HTMLFormElement>;
  onSelect: (session: Session) => void;
}

export function SessionSidebar({
  sessions, selected, workspace, onWorkspaceChange, onCreate, onSelect,
}: Props) {
  return (
    <aside className="sessions-panel panel">
      <div className="panel-heading">
        <div><span className="section-note">Local workspaces</span><strong>Recent sessions</strong></div>
        <b>{sessions.length}</b>
      </div>
      <form className="new-session" onSubmit={onCreate}>
        <label htmlFor="workspace">Open a workspace</label>
        <input
          id="workspace"
          value={workspace}
          onChange={(event) => onWorkspaceChange(event.target.value)}
          placeholder="/path/to/project"
        />
        <button type="submit" aria-label="New session"><span>＋</span> New session</button>
      </form>
      <div className="session-list">
        {sessions.map((session) => (
          <button
            className={selected?.session_id === session.session_id ? "selected" : ""}
            key={session.session_id}
            onClick={() => onSelect(session)}
          >
            <span>{session.workspace.split("/").filter(Boolean).at(-1) || "/"}</span>
            <small>{session.message_count} messages · {session.approval_mode}</small>
          </button>
        ))}
        {!sessions.length && <p className="empty-note">Create a session to begin.</p>}
      </div>
    </aside>
  );
}
