import { createJsonRequester } from './client'
import { apiErrorMessage } from './http'
import type { ToolRunFilters, ToolRunList, ToolRunRecord } from './tools'
import { generationDetailPath, generationTaskListPath } from '../lib/generationTaskHistory'

const request = createJsonRequester((response, detail) => {
  if (response.status === 401) throw new Error('未登录')
  throw new Error(apiErrorMessage(detail, '加载任务失败'))
})

/** 当前创作空间的图片、音频和视频记录；旧文字筛选兼容为全部媒体。 */
export function listGenerationTasks(page = 1, pageSize = 8, filters: ToolRunFilters = {}): Promise<ToolRunList> {
  return request(generationTaskListPath(page, pageSize, filters))
}

export function getGenerationTaskDetail(record: Pick<ToolRunRecord, 'id' | 'source'>): Promise<ToolRunRecord> {
  return request(generationDetailPath(record))
}
