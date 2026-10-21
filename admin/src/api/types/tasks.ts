import type { PageMeta } from "./common";
import type { AdminUsageEventBrief } from "./finance";

/** 统一任务平台的任务、步骤、事件与运行统计接口契约。 */
export type AdminTaskRow = {
  id: number;
  domain: string;
  task_type: string;
  status: string;
  priority: number;
  requested_by: number;
  user_email?: string | null;
  progress_percent: number;
  cancel_requested: boolean;
  cancelable: boolean;
  current_step_key?: string | null;
  current_step_status?: string | null;
  next_action_at?: string | null;
  provider_task_id?: string | null;
  error_message?: string | null;
  project_id?: number | null;
  drama_project_id?: number | null;
  episode_id?: number | null;
  fragment_id?: number | null;
  asset_id?: number | null;
  script_id?: number | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at?: string | null;
  billing_estimate_fen?: number;
  billing_charged_fen?: number;
  billing_refunded_fen?: number;
  billing_status?: string;
  /** 提交参数（入队 payload） */
  payload?: Record<string, unknown> | null;
  /** 执行结果 */
  result_payload?: Record<string, unknown> | null;
  steps?: AdminTaskStep[];
  events?: AdminTaskEvent[];
};

export type AdminTaskStep = {
  id: number;
  step_key: string;
  step_type: string;
  status: string;
  attempt_count: number;
  provider_name?: string | null;
  provider_task_id?: string | null;
  input_payload?: Record<string, unknown> | null;
  output_payload?: Record<string, unknown> | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

export type AdminTaskEvent = {
  id: number;
  event_type: string;
  status?: string | null;
  phase?: string | null;
  message?: string | null;
  payload?: Record<string, unknown> | null;
  created_at?: string | null;
};

export type AdminTaskDetail = AdminTaskRow & {
  client_request_id?: string | null;
  dedupe_key?: string | null;
  batch_key?: string | null;
  provider_task_id?: string | null;
  scheduled_at?: string | null;
  next_action_at?: string | null;
  lease_until?: string | null;
  error_code?: string | null;
  payload?: Record<string, unknown> | null;
  result_payload?: Record<string, unknown> | null;
  script_id?: number | null;
  asset_id?: number | null;
  shot_id?: number | null;
  updated_at?: string | null;
  steps: AdminTaskStep[];
  events: AdminTaskEvent[];
  targets: Array<{ id: number; target_type: string; target_id: number }>;
  usage_lines?: AdminUsageEventBrief[];
  billing_estimate_fen?: number;
  billing_charged_fen?: number;
  billing_refunded_fen?: number;
  billing_status?: string;
};

export type AdminTaskStats = {
  pending_count: number;
  pending_ready_count?: number;
  pending_deferred_count?: number;
  pending_delayed_count?: number;
  oldest_ready_wait_sec?: number;
  active_count: number;
  leased_count: number;
  running_count: number;
  awaiting_poll_count: number;
  cancel_requested_count: number;
  succeeded_count: number;
  failed_count: number;
  cancelled_count: number;
  scheduler_running_jobs: number;
  max_concurrency: number;
  scheduler?: string;
  poller?: string;
  watchdog?: string;
  runtime_healthy?: boolean;
  scheduler_success_age_sec?: number | null;
  scheduler_consecutive_failures?: number;
  domains: Array<{
    domain: string;
    pending: number;
    active: number;
    succeeded: number;
    failed: number;
    cancelled: number;
  }>;
  fetched_at?: string | null;
};

export type AdminTaskListRes = {
  items: AdminTaskRow[];
  meta: PageMeta;
};
