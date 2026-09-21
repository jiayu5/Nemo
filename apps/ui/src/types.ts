export type ApprovalMode = "ask" | "auto" | "full";

export interface Session {
  session_id: string;
  workspace: string;
  model: string | null;
  approval_mode: ApprovalMode;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ToolResult {
  tool_call_id: string;
  output: unknown;
  error: { code: string; message: string } | null;
}

export interface Message {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  tool_calls: ToolCall[];
  tool_result: ToolResult | null;
}

export interface Run {
  run_id: string;
  session_id: string;
  status: string;
  max_steps: number;
  output: string | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  model_selection: string | null;
  model_name: string | null;
  model_id: string | null;
  model_protocol: string | null;
  model_provider: string | null;
}

export interface RunEvent {
  run_id: string;
  seq: number;
  step: number;
  type: string;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface ModelOption {
  selection: string;
  kind: "profile" | "alias" | "model";
  model_name: string;
  model_id: string;
  provider_id: string;
  protocol: string;
  capabilities: string[];
  is_default: boolean;
}

export interface Provider {
  provider_id: string;
  protocol: string;
  base_url: string;
  api_key_env: string;
  secret_configured: boolean;
  timeout_seconds: number;
  models: string[];
}

export interface ApprovalRequest {
  request_id: string;
  tool_name: string;
  summary: string;
  reason: string;
}

export interface Trace {
  run: Run;
  duration_ms: number | null;
  steps: Array<{
    step: number;
    started_at: string | null;
    finished_at: string | null;
    duration_ms: number | null;
  }>;
  model_calls: Array<{
    step: number;
    started_at: string;
    finished_at: string | null;
    duration_ms: number | null;
    status: string;
    prompt_tokens: number | null;
    cached_prompt_tokens: number | null;
    completion_tokens: number | null;
  }>;
  tool_calls: Array<{
    tool_call_id: string;
    step: number;
    name: string;
    summary: string | null;
    status: string;
    error_code: string | null;
    started_at: string;
    finished_at: string | null;
    duration_ms: number | null;
  }>;
  events: RunEvent[];
}
