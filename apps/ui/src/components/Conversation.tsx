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

function roleLabel(message: Message) {
  return message.content.startsWith("# Saved memory index (application data)\n")
    ? "Memory update"
    : message.role;
}

function toolResultLabel(message: Message, names: Map<string, string>) {
  const name = names.get(message.tool_result?.tool_call_id ?? "") ?? "Tool";
  const error = message.tool_result?.error;
  return error ? `${name} · failed (${error.code})` : `${name} · result`;
}

type TurnStep =
  | { key: string; kind: "reasoning"; text: string }
  | { key: string; kind: "request"; names: string[]; body: string }
  | { key: string; kind: "result"; label: string; body: string }
  | { key: string; kind: "text"; text: string };

interface Turn {
  key: string;
  steps: TurnStep[];
  answer: string | null;
}

type Item = { key: string; kind: "message"; message: Message } | ({ kind: "turn" } & Turn);

/** One assistant turn renders as a single collapsed process block plus the final answer. */
function buildItems(messages: Message[]): Item[] {
  const names = new Map<string, string>();
  messages.forEach((message) =>
    message.tool_calls.forEach((call) => names.set(call.id, call.name)),
  );
  const items: Item[] = [];
  let turn: (Turn & { kind: "turn" }) | null = null;

  messages.forEach((message, index) => {
    const key = `${message.role}-${index}`;
    if (message.role !== "assistant" && message.role !== "tool") {
      turn = null;
      items.push({ key, kind: "message", message });
      return;
    }
    if (turn === null) {
      turn = { key: `turn-${index}`, kind: "turn", steps: [], answer: null };
      items.push(turn);
    }
    if (message.role === "tool") {
      turn.steps.push({ key: `${key}-result`, kind: "result",
        label: toolResultLabel(message, names),
        body: JSON.stringify(message.tool_result, null, 2) });
      return;
    }
    if (message.reasoning_content) {
      turn.steps.push({ key: `${key}-reasoning`, kind: "reasoning", text: message.reasoning_content });
    }
    if (message.tool_calls.length > 0) {
      turn.steps.push({ key: `${key}-request`, kind: "request",
        names: message.tool_calls.map((call) => call.name),
        body: message.tool_calls
          .map((call) => `${call.name} ${JSON.stringify(call.arguments)}`)
          .join("\n") });
    }
    if (message.content) {
      // Text next to a tool call only continues the turn; the reply is the message without calls.
      if (message.tool_calls.length > 0) {
        turn.steps.push({ key: `${key}-text`, kind: "text", text: message.content });
      } else {
        if (turn.answer) turn.steps.push({ key: `${key}-text`, kind: "text", text: turn.answer });
        turn.answer = message.content;
      }
    }
  });
  return items;
}

function turnSummary(steps: TurnStep[]) {
  const reasoning = steps.filter((step) => step.kind === "reasoning").length;
  const requests = steps.filter((step) => step.kind === "request").length;
  const calls = requests || steps.filter((step) => step.kind === "result").length;
  const parts: string[] = [];
  if (reasoning) parts.push("Reasoning");
  if (calls) parts.push(`${calls} tool call${calls === 1 ? "" : "s"}`);
  return parts.join(" · ") || "Details";
}

function TurnSteps({ steps }: { steps: TurnStep[] }) {
  return (
    <>
      {steps.map((step) => {
        // A lone reasoning block already says so in its summary; tool labels carry names.
        const showLabel = steps.length > 1 || step.kind === "request" || step.kind === "result";
        return (
          <div className="turn-step" key={step.key}>
            {step.kind === "reasoning" && (
              <>
                {showLabel && <span className="turn-step-label">Reasoning</span>}
                <div className="turn-step-text">{step.text}</div>
              </>
            )}
            {step.kind === "request" && (
              <>
                <span className="turn-step-label">Requested {step.names.join(", ")}</span>
                <pre>{step.body}</pre>
              </>
            )}
            {step.kind === "result" && (
              <>
                <span className="turn-step-label">{step.label}</span>
                <pre>{step.body}</pre>
              </>
            )}
            {step.kind === "text" && (
              <div className="turn-step-body">
                <Markdown remarkPlugins={[remarkGfm]}>{step.text}</Markdown>
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}

export function Conversation({
  selected, messages, liveText, liveReasoning, models, activeRun, approval, prompt, onPromptChange, onSubmit,
  onStop, onApproval, onModeChange, onModelChange,
}: Props) {
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const items = buildItems(messages);

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
      </div>

      <div className="messages" aria-label="Conversation messages">
        {items.filter((item) => item.kind === "message" || item.steps.length > 0 || item.answer).map((item) => (
          item.kind === "message" ? (
            <article className={`message ${item.message.role}`} key={item.key}>
              <span className="role">{roleLabel(item.message)}</span>
              <div className="message-content">
                <Markdown remarkPlugins={[remarkGfm]}>{item.message.content}</Markdown>
              </div>
            </article>
          ) : (
            <article className="message assistant" key={item.key}>
              {(item.steps.length > 0 || item.answer) && <span className="role">assistant</span>}
              {item.steps.length > 0 && (
                <details className="turn-block">
                  <summary>{turnSummary(item.steps)}</summary>
                  <TurnSteps steps={item.steps} />
                </details>
              )}
              {item.answer && (
                <div className="message-content">
                  <Markdown remarkPlugins={[remarkGfm]}>{item.answer}</Markdown>
                </div>
              )}
            </article>
          )
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
        onStop={onStop}
        onModeChange={onModeChange}
        onModelChange={onModelChange}
      />
    </section>
  );
}
