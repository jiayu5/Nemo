import type { FormEventHandler, KeyboardEvent } from "react";
import type { ApprovalMode, ModelOption, Run, Session } from "../types";

interface Props {
  selected: Session | null;
  models: ModelOption[];
  activeRun: Run | null;
  prompt: string;
  onPromptChange: (value: string) => void;
  onSubmit: FormEventHandler<HTMLFormElement>;
  onModeChange: (mode: ApprovalMode) => void;
  onModelChange: (model: string) => void;
}

export function Composer({
  selected, models, activeRun, prompt, onPromptChange, onSubmit, onModeChange, onModelChange,
}: Props) {
  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <form className="composer" onSubmit={onSubmit}>
      <div className="composer-surface">
        <textarea
          value={prompt}
          onChange={(event) => onPromptChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={selected ? "Ask anything" : "Create a session first"}
          disabled={!selected || Boolean(activeRun)}
          rows={3}
        />
        <div className="composer-toolbar">
          <button className="composer-add" type="button" aria-label="Add context">＋</button>
          <label className="composer-control approval-control">
            <span className="approval-icon" aria-hidden="true">!</span>
            <select
              aria-label="Approval"
              value={selected?.approval_mode ?? "ask"}
              disabled={!selected || Boolean(activeRun)}
              onChange={(event) => onModeChange(event.target.value as ApprovalMode)}
            >
              <option value="ask">Ask</option>
              <option value="auto">Auto</option>
              <option value="full">Full</option>
            </select>
          </label>
          <label className="composer-control model-control">
            <select
              aria-label="Model"
              value={selected?.model ?? models.find((item) => item.is_default)?.selection ?? ""}
              disabled={!selected || Boolean(activeRun)}
              onChange={(event) => onModelChange(event.target.value)}
            >
              {models.map((model) => (
                <option key={model.selection} value={model.selection}>
                  {model.selection} · {model.provider_id}
                </option>
              ))}
            </select>
          </label>
          <button
            className="composer-send"
            type="submit"
            aria-label="Run task"
            disabled={!selected || !prompt.trim() || Boolean(activeRun)}
          >
            {activeRun ? <span className="running-spinner" /> : "↑"}
          </button>
        </div>
      </div>
    </form>
  );
}
