import { useLayoutEffect, useRef } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { RunEvent } from "../types";
import { useFollowScroll } from "../hooks/useFollowScroll";

type ToolDetail = { id: string; name: string; arguments: string; summary: string; result: string; status: string };
type Step = { round: number; number: number; text: string; reasoning: string; tools: Map<string, ToolDetail>; done: boolean };
function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}
function text(value: unknown) { return typeof value === "string" ? value : ""; }

export function SubagentView({ runId, events, onClose }: {
  runId: string; events: RunEvent[]; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useLayoutEffect(() => { dialog.current?.showModal(); }, []);
  const scroll = useFollowScroll(runId, [events]);
  const steps = new Map<number, Step>();
  let task = "", status = "queued", error = "", currentRound = 1;
  const rounds = new Map<number, { prompt: string; status: string; output: string; chunks: Map<number, string> }>();
  const timeline: { key: number; type: string; step: number }[] = [];
  for (const event of events) {
    if (event.type !== "subagent.event" || event.payload.child_run_id !== runId) continue;
    const type = text(event.payload.child_type);
    const payload = record(event.payload.child_payload);
    const number = Number(event.payload.child_step ?? 0);
    const roundNumber = Number(payload.round ?? 1);
    currentRound = Math.max(currentRound, roundNumber);
    const round = rounds.get(roundNumber) ?? { prompt: "", status: "queued", output: "", chunks: new Map<number, string>() };
    if (!["model.delta", "model.reasoning_delta"].includes(type)) timeline.push({ key: event.seq, type, step: number });
    if (type === "subagent.queued") task = text(payload.task);
    if (type === "subagent.resumed") { round.prompt = text(payload.prompt); status = "queued"; error = ""; }
    if (type === "run.started") status = "running";
    if (type.startsWith("run.") && type !== "run.started") status = type.slice(4);
    if (type === "subagent.report") round.chunks.set(Number(payload.offset), text(payload.text));
    if (type === "subagent.finished") {
      status = text(payload.status) || status;
      round.output = text(payload.output);
      error = text(payload.error);
    }
    round.status = status;
    rounds.set(roundNumber, round);
    if (!number || type.startsWith("subagent.")) continue;
    const step = steps.get(number) ?? { round: roundNumber, number, text: "", reasoning: "", tools: new Map(), done: false };
    if (type === "model.delta") step.text += text(payload.text);
    if (type === "model.reasoning_delta") step.reasoning += text(payload.text);
    if (type === "model.completed") {
      step.text = step.text || text(payload.content);
      step.reasoning = step.reasoning || text(payload.reasoning_content);
      if (Array.isArray(payload.tool_calls)) for (const raw of payload.tool_calls) {
        const call = record(raw), id = text(call.id);
        step.tools.set(id, { id, name: text(call.name), arguments: text(call.arguments), summary: "", result: "", status: "requested" });
      }
    }
    if (type.startsWith("tool.")) {
      const id = text(payload.tool_call_id);
      const tool = step.tools.get(id) ?? { id, name: "Tool", arguments: "", summary: "", result: "", status: "requested" };
      tool.name = text(payload.name) || tool.name;
      tool.summary = text(payload.summary) || tool.summary;
      tool.result = text(payload.result) || tool.result;
      tool.status = type.slice(5);
      step.tools.set(id, tool);
    }
    if (type === "step.completed") step.done = true;
    steps.set(number, step);
  }
  const terminal = !["running", "queued"].includes(status);
  return <dialog className="subagent-dialog" ref={dialog} onCancel={onClose} aria-label={`Subagent ${runId}`}>
    <header className="subagent-heading"><div><h2>Subagent {runId.slice(0, 7)}</h2>
      <p>{status} · read-only{currentRound > 1 ? ` · round ${currentRound}` : ""}</p></div>
      <button type="button" onClick={onClose}>Back to conversation</button></header>
    <div className="subagent-body" ref={scroll.ref} onScroll={scroll.onScroll} onWheel={scroll.onWheel} aria-label="Subagent execution">
      <section className="subagent-task"><h3>Task</h3><p>{task || "Task details were not recorded for this run."}</p></section>
      {[...rounds.entries()].map(([roundNumber, round]) => <section className="subagent-round" key={roundNumber}>
      {rounds.size > 1 && <h3>Round {roundNumber} · {round.status}</h3>}
      {round.prompt && <section className="subagent-task"><h3>Continuation instructions</h3><p>{round.prompt}</p></section>}
      {[...steps.values()].filter(step => step.round === roundNumber).map(step => <section className="subagent-step" key={step.number}>
        <h3>Step {step.number}{!step.done && !terminal ? " · running" : ""}</h3>
        {step.reasoning && <details className="reasoning-block"><summary>Reasoning</summary><div className="reasoning-content">{step.reasoning}</div></details>}
        {step.text && <div className="message-content"><Markdown remarkPlugins={[remarkGfm]}>{step.text}</Markdown></div>}
        {[...step.tools.values()].map(tool => <details className="turn-block" key={tool.id}>
          <summary>{tool.name} · {tool.status}</summary>
          {tool.summary && <p>{tool.summary}</p>}
          {tool.arguments && <><span className="turn-step-label">Arguments (excerpt)</span><pre>{tool.arguments}</pre></>}
          {tool.result && <><span className="turn-step-label">Result (excerpt)</span><pre>{tool.result}</pre></>}
          {!tool.result && terminal && <p>No result details were recorded.</p>}
        </details>)}
      </section>)}
      {(round.output || round.chunks.size > 0) && <section className="subagent-report"><h3>{round.status === "limit_reached" ? "Partial report" : "Report"}</h3>
        <div className="message-content"><Markdown remarkPlugins={[remarkGfm]}>{round.chunks.size
          ? [...round.chunks.entries()].sort(([a], [b]) => a - b).map(([, value]) => value).join("")
          : round.output}</Markdown></div></section>}
      </section>)}
      {error && <p role="alert" className="error-banner">{error}</p>}
      <details className="subagent-timeline"><summary>Event timeline</summary>
        {timeline.map(event => <p key={event.key}>Step {event.step} · {event.type}</p>)}
      </details>
    </div>
    {scroll.paused && <button className="jump-to-latest" type="button" onClick={scroll.resume}>Jump to latest</button>}
  </dialog>;
}
