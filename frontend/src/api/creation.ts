import type { Project } from '../api'
import { createJsonRequester } from './client'
import { throwApiError } from '../lib/apiError'
import { creationApiErrorDetail } from '../lib/creationApiError'

const request = createJsonRequester((response, detail) =>
  throwApiError(response.status, creationApiErrorDetail(response.status, detail), response.statusText),
)

export type CreationMessage = {
  id: number | string
  role: string
  content: string
  created_at?: string
}

export type CreationRun = {
  id: number | string
  version: number
  status: string
  phase: string
  copy_text?: string | null
  error_message?: string | null
  project_id?: number | null
  project?: Project | null
}

export type CreationSession = {
  id: number | string
  title: string
  status: string
  template_id: string
  messages: CreationMessage[]
  runs: CreationRun[]
  active_run_id?: number | string | null
  brief?: Record<string, unknown> | null
}

export type CreationSessionSummary = Pick<
  CreationSession,
  'id' | 'title' | 'status' | 'template_id'
> & {
  updated_at?: string
}

type MessageInput = {
  content: string
  client_message_id: string
  template_id?: string
}
const path = (id: number | string) =>
  `/api/creation-sessions/${encodeURIComponent(id)}`

export const creationApi = {
  openEditor(id: number | string, signal?: AbortSignal, projectId?: number) {
    const query = projectId ? `?project_id=${projectId}` : ''
    return request<Project>(`${path(id)}/open-editor${query}`, { method: 'POST', signal })
  },
  async list(limit = 5, signal?: AbortSignal) {
    const value = await request<
      CreationSessionSummary[] | { items: CreationSessionSummary[] }
    >(`/api/creation-sessions?limit=${limit}`, { signal })
    return Array.isArray(value)
      ? value
      : Array.isArray(value.items)
        ? value.items
        : []
  },
  create(
    body: {
      template_id?: string
      message?: string
      client_message_id?: string
    },
    signal?: AbortSignal,
  ) {
    return request<CreationSession>('/api/creation-sessions', {
      method: 'POST',
      body: JSON.stringify(body),
      signal,
    })
  },
  get(id: number | string, signal?: AbortSignal) {
    return request<CreationSession>(path(id), { signal })
  },
  send(id: number | string, body: MessageInput, signal?: AbortSignal) {
    return request<CreationSession>(`${path(id)}/messages`, {
      method: 'POST',
      body: JSON.stringify(body),
      signal,
    })
  },
  cancel(id: number | string, signal?: AbortSignal) {
    return request<CreationSession>(`${path(id)}/cancel`, {
      method: 'POST',
      signal,
    })
  },
  retry(id: number | string, signal?: AbortSignal) {
    return request<CreationSession>(`${path(id)}/retry`, {
      method: 'POST',
      signal,
    })
  },
}
