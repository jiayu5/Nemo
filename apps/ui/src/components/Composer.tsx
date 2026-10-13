import { useRef } from "react";
import type { FormEventHandler, KeyboardEvent } from "react";
import type { ApprovalMode, ModelOption, Run, Session, ImageRef } from "../types";
import { MessageImages } from "./MessageImages";

interface Props {
  selected: Session | null;
  models: ModelOption[];
  activeRun: Run | null;
  prompt: string;
  onPromptChange: (value: string) => void;
  onSubmit: FormEventHandler<HTMLFormElement>;
  onStop: () => void;
  onModeChange: (mode: ApprovalMode) => void;
  onModelChange: (model: string) => void;
  images?: ImageRef[];
  uploading?: boolean;
  submitting?: boolean;
  onAddImages?: (files: File[]) => void;
  onRemoveImage?: (index: number) => void;
}

export function Composer({
  selected, models, activeRun, prompt, onPromptChange, onSubmit, onStop, onModeChange, onModelChange,
  images = [], uploading = false, submitting = false, onAddImages, onRemoveImage,
}: Props) {
  const fileInput = useRef<HTMLInputElement>(null);
  const busy = Boolean(activeRun) || uploading || submitting;
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
      <div className="composer-surface"
        onDragOver={(event) => { if (onAddImages && !busy) event.preventDefault(); }}
        onDrop={(event) => { if (!onAddImages || busy) return; event.preventDefault(); onAddImages(Array.from(event.dataTransfer.files)); }}>
        {selected && images.length > 0 && <div className="composer-attachments">
          <MessageImages sessionId={selected.session_id} images={images} />
          <div className="attachment-actions">{images.map((image, index) =>
            <button key={`${image.asset_id}-${index}`} type="button" disabled={busy}
              onClick={() => onRemoveImage?.(index)} aria-label={`Remove image ${index + 1}`}>Remove {index + 1}</button>)}</div>
        </div>}
        {uploading && <span role="status">Uploading images…</span>}
        <input ref={fileInput} type="file" hidden multiple accept="image/png,image/jpeg,image/webp"
          aria-label="Attach images" disabled={!selected || busy}
          onChange={(event) => { onAddImages?.(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
        <textarea
          value={prompt}
          onChange={(event) => onPromptChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={selected ? "Ask anything" : "Create a session first"}
          disabled={!selected || busy}
          onPaste={(event) => {
            const files = Array.from(event.clipboardData.items).filter((item) => item.kind === "file")
              .map((item) => item.getAsFile()).filter((file): file is File => file !== null);
            if (files.length && onAddImages) { event.preventDefault(); onAddImages(files); }
          }}
          rows={3}
        />
        <div className="composer-toolbar">
          <button className="composer-add" type="button" aria-label="Add context" title="Attach images"
            disabled={!selected || busy || !onAddImages} onClick={() => fileInput.current?.click()}>＋</button>
          <label className="composer-control approval-control">
            <span className="approval-icon" aria-hidden="true">!</span>
            <select
              aria-label="Approval"
              value={selected?.approval_mode ?? "ask"}
              disabled={!selected || busy}
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
              disabled={!selected || busy || actualModels.length === 0}
              onChange={(event) => onModelChange(event.target.value)}
            >
              {actualModels.map((model) => (
                <option key={model.selection} value={model.selection}>
                  {model.model_name} · {model.provider_id}
                </option>
              ))}
            </select>
          </label>
          {activeRun ? (
            <button
              className="composer-send composer-stop"
              type="button"
              aria-label="Stop"
              title="Stop the running turn"
              onClick={onStop}
            >
              <span className="stop-glyph" aria-hidden="true" />
            </button>
          ) : (
            <button
              className="composer-send"
              type="submit"
              aria-label="Run task"
              disabled={!selected || busy || (!prompt.trim() && images.length === 0)}
            >
              ↑
            </button>
          )}
        </div>
      </div>
    </form>
  );
}
