import { createJsonRequester } from './client'
import { apiErrorMessage } from './http'
import type { ToolRunFilters, ToolRunList, ToolRunRecord } from './tools'
import { generationDetailPath } from '../lib/generationTaskHistory'

const request = createJsonRequester((response, detail) => {
  if (response.status === 401) throw new Error('未登录')
  throw new Error(apiErrorMessage(detail, '加载任务失败'))
})

/** 当前创作空间的全部生成记录；不传 kind 时包含文字、图片、音频和视频。 */
export function listGenerationTasks(page = 1, pageSize = 8, filters: ToolRunFilters = {}): Promise<ToolRunList> {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  for (const [key, value] of Object.entries(filters)) if (value) query.set(key, value)
  return request(`/api/tasks/generations?${query}`)
}

export function getGenerationTaskDetail(record: Pick<ToolRunRecord, 'id' | 'source'>): Promise<ToolRunRecord> {
  return request(generationDetailPath(record))
}
