import { useState } from "react";
import { Conversation } from "./components/Conversation";
import { InspectorPanel } from "./components/InspectorPanel";
import type { InspectorView } from "./components/InspectorPanel";
import { SessionSidebar } from "./components/SessionSidebar";
import { useNemoWorkspace } from "./hooks/useNemoWorkspace";

export default function App() {
  const workspace = useNemoWorkspace();
  const [inspectorView, setInspectorView] = useState<InspectorView>("activity");

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
          onClick={() => setInspectorView("connections")}
        >Connections</button>
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
          onCreate={workspace.createSession}
          onSelect={(session) => void workspace.selectSession(session)}
        />
        <Conversation
          selected={workspace.selected}
          messages={workspace.messages}
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
          view={inspectorView}
          onViewChange={setInspectorView}
          runs={workspace.runs}
          trace={workspace.trace}
          events={workspace.events}
          settings={workspace.providerSettings}
          onSelectRun={(run) => void workspace.selectRun(run)}
          onSettingsSaved={workspace.refreshConfiguration}
        />
      </main>

      <footer className="statusbar">
        <span className="workspace-status"><i /><b>Workspace</b> {workspace.selected?.workspace ?? "—"}</span>
      </footer>
    </div>
  );
}
