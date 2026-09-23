import { useEffect, useRef } from "react";
import type { FormEventHandler } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type {
  ApprovalMode,
  ApprovalRequest,
  Message,
  ModelOption,
  Run,
  Session,
} from "../types";
import { Composer } from "./Composer";

interface Props {
  selected: Session | null;
  messages: Message[];
  liveText: string;
  liveReasoning: string;
  models: ModelOption[];
  activeRun: Run | null;
  approval: ApprovalRequest | null;
  prompt: string;
  onPromptChange: (value: string) => void;
  onSubmit: FormEventHandler<HTMLFormElement>;
  onStop: () => void;
  onApproval: (outcome: "allow_once" | "allow_session" | "deny") => void;
  onModeChange: (mode: ApprovalMode) => void;
  onModelChange: (model: string) => void;
}

export function Conversation({
  selected, messages, liveText, liveReasoning, models, activeRun, approval, prompt, onPromptChange, onSubmit,
  onStop, onApproval, onModeChange, onModelChange,
}: Props) {
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages, liveText, liveReasoning]);

  return (
    <section className="conversation-panel panel">
      <div className="panel-heading conversation-title">
        <div>
          <span>{selected ? "New task" : "No session selected"}</span>
          <small>{selected?.workspace ?? "Choose a workspace on the left"}</small>
        </div>
        {activeRun && <button className="danger-button" onClick={onStop}>Stop</button>}
      </div>

      <div className="messages" aria-label="Conversation messages">
        {messages.map((message, index) => (
          <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
            <span className="role">{message.role}</span>
            {message.role === "assistant" && message.reasoning_content && (
              <details className="reasoning-block">
                <summary>Reasoning</summary>
                <div className="reasoning-content">{message.reasoning_content}</div>
              </details>
            )}
            {message.role === "tool" ? (
              <pre>{JSON.stringify(message.tool_result, null, 2)}</pre>
            ) : (
              <div className="message-content">
                <Markdown remarkPlugins={[remarkGfm]}>
                  {message.content ||
                    (message.tool_calls.length
                      ? `Requested: ${message.tool_calls.map((call) => call.name).join(", ")}`
                      : "")}
                </Markdown>
              </div>
            )}
          </article>
        ))}
        {(liveText || liveReasoning) && (
          <article className="message assistant" aria-label="Streaming assistant response">
            <span className="role">assistant</span>
            {liveReasoning && (
              <details className="reasoning-block" open={!liveText}>
                <summary>Reasoning · generating</summary>
                <div className="reasoning-content">{liveReasoning}</div>
              </details>
            )}
            {liveText && <div className="message-content"><Markdown remarkPlugins={[remarkGfm]}>{liveText}</Markdown></div>}
          </article>
        )}
        {!messages.length && selected && (
          <div className="conversation-empty">
            <div className="empty-orb"><span>N</span></div>
            <p className="empty-kicker">Runtime ready</p>
            <h2>What should we work on?</h2>
            <p className="empty-copy">Ask Nemo to inspect code, make a change, or explain this workspace.</p>
            <div className="suggestion-row">
              <button type="button" onClick={() => onPromptChange("Summarize this project and its architecture")}>Summarize project</button>
              <button type="button" onClick={() => onPromptChange("Find the highest-priority improvement to make next")}>Suggest next step</button>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {approval && (
        <section className="approval-card">
          <div><strong>Approval required</strong><span>{approval.tool_name}</span></div>
          <p>{approval.summary || approval.reason}</p>
          <div className="approval-actions">
            <button onClick={() => onApproval("allow_once")}>Allow once</button>
            <button onClick={() => onApproval("allow_session")}>Allow session</button>
            <button className="danger-button" onClick={() => onApproval("deny")}>Deny</button>
          </div>
        </section>
      )}

      <Composer
        selected={selected}
        models={models}
        activeRun={activeRun}
        prompt={prompt}
        onPromptChange={onPromptChange}
        onSubmit={onSubmit}
        onModeChange={onModeChange}
        onModelChange={onModelChange}
      />
    </section>
  );
}
