import type { Project } from '../api'
import { createJsonRequester } from './client'
import { throwApiError } from '../lib/apiError'
import { creationApiErrorDetail } from '../lib/creationApiError'

const request = createJsonRequester((response, detail) =>
  throwApiError(response.status, creationApiErrorDetail(response.status, detail), response.statusText),
)

const path = (id: number | string) =>
  `/api/creation-sessions/${encodeURIComponent(id)}`

/** 历史会话移交分步工作台时仍需保留此兼容接口。 */
export const creationApi = {
  openEditor(id: number | string, signal?: AbortSignal, projectId?: number) {
    const query = projectId ? `?project_id=${projectId}` : ''
    return request<Project>(`${path(id)}/open-editor${query}`, { method: 'POST', signal })
  },
}
