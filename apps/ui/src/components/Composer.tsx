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
  const actualModels = models.filter((model) => model.kind === "model");
  const selectedOption = models.find((model) => model.selection === selected?.model);
  const selectedModel = actualModels.find(
    (model) => model.model_name === selectedOption?.model_name,
  )?.selection;
  const defaultModel = actualModels.find((model) => model.is_default)?.selection
    ?? actualModels.find(
      (model) => model.model_name === models.find((item) => item.is_default)?.model_name,
    )?.selection
    ?? actualModels[0]?.selection
    ?? "";

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
              value={selectedModel ?? defaultModel}
              disabled={!selected || Boolean(activeRun) || actualModels.length === 0}
              onChange={(event) => onModelChange(event.target.value)}
            >
              {actualModels.map((model) => (
                <option key={model.selection} value={model.selection}>
                  {model.model_name} · {model.provider_id}
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
