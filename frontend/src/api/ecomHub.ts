import { createJsonRequester } from './client'
import type { DaihuoProjectPayload, DaihuoVideoQuote } from './ecomSet'
import type { Shot } from '../api'
import type { EcomKind } from '../lib/ecomHub'

/* 聚合历史和批量图片带货客户端，沿用统一请求与鉴权。 */
const BASE = '/api/tools/ecom'
const request = createJsonRequester()
export type RecentEcomTask = { id: number; kind: EcomKind; title: string; status: string; created_at: string }
export type EcomRecent = { items: RecentEcomTask[]; meta: { page: number; page_size: number; total: number } }

/** 在服务端按类型筛选并合并两类历史，不在浏览器拼接分页。 */
export function listRecentEcom(page: number, pageSize: number, kind: EcomKind | ''): Promise<EcomRecent> {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (kind) query.set('kind', kind)
  return request(`${BASE}/recent?${query}`)
}

/** 读取批量结果最近创建的视频，未创建时为空。 */
export function getJobVideo(id: number): Promise<{ project_id: number | null }> {
  return request(`${BASE}/jobs/${id}/project`)
}

/** 复用图片创建带货草稿，显式新建使用请求键幂等。 */
export function createJobVideo(id: number, payload: DaihuoProjectPayload & { name?: string; notes?: string }): Promise<{ project_id: number }> {
  return request(`${BASE}/jobs/${id}/project`, { method: 'POST', body: JSON.stringify(payload) })
}

/** 报价包含写稿和后续配音，已有图片不收生成费。 */
export function quoteJobVideo(id: number, payload: DaihuoProjectPayload, signal?: AbortSignal): Promise<DaihuoVideoQuote> {
  return request(`${BASE}/jobs/${id}/project/quote`, { method: 'POST', body: JSON.stringify(payload), signal })
}


/** 只启动带货脚本阶段；已有分镜或任务时返回原项目，不自动推进配音。 */
export function startDaihuoScript(id: number): Promise<{ project_id: number }> {
  return request(`${BASE}/projects/${id}/script`, { method: 'POST' })
}

/** 带货分镜切换动态/静图与镜头动作；只改标记，视频在分镜页继续生成时才提交。 */
export function setShotMotion(projectId: number, shotId: number, body: { motion: boolean; action?: string; detail?: string }): Promise<Shot> {
  return request(`${BASE}/projects/${projectId}/shots/${shotId}/motion`, { method: 'PATCH', body: JSON.stringify(body) })
}
