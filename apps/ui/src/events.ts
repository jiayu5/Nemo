export const RUN_EVENT_TYPES = [
  "run.started",
  "run.completed",
  "run.failed",
  "run.cancelled",
  "run.interrupted",
  "run.limit_reached",
  "step.started",
  "step.completed",
  "model.started",
  "model.completed",
  "tool.started",
  "tool.completed",
  "tool.failed",
  "approval.requested",
  "approval.resolved",
  "observer.failed",
] as const;

const TERMINAL_RUN_EVENTS = new Set<string>([
  "run.completed",
  "run.failed",
  "run.cancelled",
  "run.interrupted",
  "run.limit_reached",
]);

export function isTerminalRunEvent(type: string): boolean {
  return TERMINAL_RUN_EVENTS.has(type);
}
