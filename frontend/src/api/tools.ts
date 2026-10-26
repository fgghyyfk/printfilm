/** 独立创作工具 API：/api/tools/* */

import { throwApiError } from '../lib/apiError'
import { API_BASE, createJsonRequester } from './client'
import { apiErrorMessage } from './http'

/** 工具请求统一处理登录失效；仅生成入口沿用全局计费提示。 */
function requestToolJson<T>(
  path: string,
  fallback: string,
  init?: RequestInit,
  notifyBilling = false,
): Promise<T> {
  return createJsonRequester((response, detail) => {
    if (response.status === 401) throw new Error('未登录')
    if (notifyBilling) throwApiError(response.status, detail, fallback)
    throw new Error(apiErrorMessage(detail, fallback))
  })<T>(path, init)
}

export type ToolRunResult = {
  kind: string
  urls: string[]
  task_id?: string | null
  status: string
  preview_url?: string | null
  message?: string | null
}

export type ToolTaskResult = {
  status: string
  kind: string
  urls: string[]
  error?: string | null
}

export type ToolRunRecord = {
  id: number
  source?: 'task' | 'tool'
  record_key?: string
  domain?: string | null
  task_type?: string | null
  text_content?: string | null
  billing_status?: string | null
  tool_id: string
  kind: string
  status: string
  prompt: string
  preview_url?: string | null
  urls: string[]
  task_id?: string | null
  params?: Record<string, string> | null
  error?: string | null
  created_at: string
  platform_task_id?: number | null
  provider_task_id?: string | null
  provider_name?: string | null
  provider_status?: string | null
  queue_type?: string | null
  started_at?: string | null
  finished_at?: string | null
  billing_charged_fen?: number | null
}

export type ToolRunList = {
  items: ToolRunRecord[]
  total: number
  page: number
  page_size: number
  counts?: Record<string, number>
}

// 将 /static 相对路径补全为可访问地址
export function resolveToolMediaUrl(url?: string | null): string {
  if (!url) return ''
  if (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('data:')) {
    return url
  }
  if (url.startsWith('/')) return `${API_BASE}${url}`
  return url
}

export type ToolRunPayload = {
  modelId?: string
  resolution?: string
  toolId: string
  prompt?: string
  negative?: string
  ratio?: string
  strength?: string
  mode?: string
  pack?: string
  duration?: string
  motion?: string
  voice?: string
  files?: File[]
}

// 提交工具生成（multipart）
export async function runStudioTool(payload: ToolRunPayload): Promise<ToolRunResult> {
  const body = new FormData()
  body.append('tool_id', payload.toolId)
  body.append('model_id', payload.modelId || '')
  body.append('resolution', payload.resolution || '')
  body.append('prompt', payload.prompt || '')
  body.append('negative', payload.negative || '')
  body.append('ratio', payload.ratio || '')
  body.append('strength', payload.strength || '')
  body.append('mode', payload.mode || '')
  body.append('pack', payload.pack || '')
  body.append('duration', payload.duration || '')
  body.append('motion', payload.motion || '')
  body.append('voice', payload.voice || 'narrator_calm')
  for (const file of payload.files || []) {
    body.append('files', file)
  }
  return requestToolJson('/api/tools/run', '生成失败', { method: 'POST', body }, true)
}

// 查询视频任务状态
export async function pollStudioToolTask(taskId: string): Promise<ToolTaskResult> {
  return requestToolJson(`/api/tools/tasks/${encodeURIComponent(taskId)}`, '查询失败')
}

// 个人中心：分页拉取工具创作记录
export type ToolRunFilters = { kind?: string; status?: string; task_id?: string; created_from?: string; created_to?: string }

export async function listToolRuns(page = 1, pageSize = 8, filters: ToolRunFilters = {}): Promise<ToolRunList> {
  const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  for (const [key, value] of Object.entries(filters)) if (value) qs.set(key, value)
  return requestToolJson(`/api/tools/runs?${qs}`, '加载失败')
}

export async function getGenerationConfig(): Promise<{ models: Record<string, string> }> {
  return requestToolJson('/api/tools/config', '加载模型配置失败')
}

// 个人中心：单条创作详情（含 OSS 结果地址）
export async function getToolRun(runId: number): Promise<ToolRunRecord> {
  return requestToolJson(`/api/tools/runs/${runId}`, '加载失败')
}
