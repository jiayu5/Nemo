import { useState } from "react";
import { SkillsPanel } from "./components/SkillsPanel";
import { MemoryPanel } from "./components/MemoryPanel";
import { Conversation } from "./components/Conversation";
import { InspectorPanel } from "./components/InspectorPanel";
import { ConnectionsDialog } from "./components/ConnectionsDialog";
import { SessionSidebar } from "./components/SessionSidebar";
import { useNemoWorkspace } from "./hooks/useNemoWorkspace";

export default function App() {
  const workspace = useNemoWorkspace();
  const [showMemory, setShowMemory] = useState(false);
  const [showSkills, setShowSkills] = useState(false);
  const [showConnections, setShowConnections] = useState(false);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <div className="brand-mark">N</div>
          <div><h1>Nemo</h1><p>Local agent runtime</p></div>
        </div>
        <div className="topbar-context">
          <span className="context-label">Current workspace</span>
          <span className="context-value">
            {workspace.selected?.workspace.split("/").filter(Boolean).at(-1) ?? "No session"}
          </span>
        </div>
        <button
          className="settings-toggle"
          type="button"
          onClick={() => setShowConnections(true)}
        >Connections</button>
        <button className="settings-toggle" type="button" onClick={() => setShowMemory(true)}>Memories</button>
        <button className="settings-toggle" type="button" onClick={() => setShowSkills(true)}>Skills</button>
        <span className={`server-pill ${workspace.serverOnline ? "online" : "offline"}`}>
          <i /> {workspace.serverOnline ? "Server online" : "Server offline"}
        </span>
      </header>

      {workspace.error && <div className="error-banner">{workspace.error}</div>}

      <main className="workspace-grid">
        <SessionSidebar
          sessions={workspace.sessions}
          selected={workspace.selected}
          workspace={workspace.workspace}
          onWorkspaceChange={workspace.setWorkspace}
          onBrowse={() => void workspace.pickWorkspace()}
          onDelete={workspace.deleteSession}
          onCreate={workspace.createSession}
          onSelect={(session) => void workspace.selectSession(session)}
        />
        <Conversation
          selected={workspace.selected}
          messages={workspace.messages}
          liveText={workspace.liveText}
          liveReasoning={workspace.liveReasoning}
          models={workspace.models}
          activeRun={workspace.activeRun}
          approval={workspace.approval}
          prompt={workspace.prompt}
          onPromptChange={workspace.setPrompt}
          onSubmit={workspace.submitPrompt}
          onStop={() => void workspace.cancelRun()}
          onApproval={(outcome) => void workspace.answerApproval(outcome)}
          onModeChange={(mode) => void workspace.updateMode(mode)}
          onModelChange={(model) => void workspace.updateModel(model)}
        />
        <InspectorPanel
          runs={workspace.runs}
          trace={workspace.trace}
          events={workspace.events}
          onSelectRun={(run) => void workspace.selectRun(run)}
        />
      </main>

      {showConnections && <ConnectionsDialog settings={workspace.providerSettings}
        onSaved={workspace.refreshConfiguration} onClose={() => setShowConnections(false)} />}

      {showSkills && <SkillsPanel key={workspace.selected?.session_id} session={workspace.selected} onClose={() => setShowSkills(false)} />}

      {showMemory && <MemoryPanel key={workspace.selected?.session_id} session={workspace.selected} onClose={() => setShowMemory(false)} />}

      <footer className="statusbar">
        <span className="workspace-status"><i /><b>Workspace</b> {workspace.selected?.workspace ?? "—"}</span>
      </footer>
    </div>
  );
}
