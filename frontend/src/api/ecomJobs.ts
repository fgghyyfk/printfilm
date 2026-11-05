import { createJsonRequester, requestResponse } from './client'
import { resolveToolMediaUrl } from './tools'
import { fetchMediaBlob, triggerBlobDownload } from '../lib/clientDownload'
import { parseDispositionFilename } from '../lib/ecomSet'

/* 通用批量出图契约；每个输入携带服务端保存的角色和可预览地址。 */
const BASE = '/api/tools/ecom-jobs'
const request = createJsonRequester()
export type EcomJobStatus = 'queued' | 'running' | 'done' | 'partial' | 'failed'
export type EcomJobItem = {
  idx: number
  inputs: { role: string; url: string }[]
  prompt: string
  ratio: string
  size: string
  result_url: string | null
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  error: string | null
  task_run_id: number | null
}
export type RefineParams = {
  background: 'white' | 'keep' | 'transparent'
  ratio: '1:1' | '3:4' | '原比例'
  clarity: '2k' | '4k'
  notes: string
}
export type StyleParams = {
  mode: 'single' | 'batch'
  count: number
  ratio: '1:1' | '3:4' | '9:16' | '16:9'
  clarity: '2k' | '4k'
  notes: string
}
export type BuyerShowParams = {
  mode: 'buyer' | 'seeding'
  count: number
  ratio: '1:1' | '3:4' | '9:16' | '16:9'
  clarity: '2k' | '4k'
  notes: string
}
export type EcomJob = {
  id: number
  kind: string
  status: EcomJobStatus
  title: string
  params: Record<string, unknown>
  items: EcomJobItem[]
  created_at: string
  updated_at: string
}
export type EcomKindOptions = {
  max_images: number
  mode_limits?: Record<string, number> | null
  max_count?: number
  ratios: string[]
  clarities: string[]
  prices: Record<string, number>
}
export type EcomJobOptions = { kinds: Record<string, EcomKindOptions>; billing_enabled: boolean }
export type EcomJobList = { items: EcomJob[]; meta: { total: number; page: number; page_size: number } }

/** 获取批量出图能力、数量限制和实时估价。 */
export function getEcomJobOptions(): Promise<EcomJobOptions> {
  return request(`${BASE}/options`)
}

/** 上传整批商品图并创建指定功能的任务。 */
export function createEcomJob(kind: string, files: File[], params: RefineParams | StyleParams | BuyerShowParams, styleFiles: File[] = [], references: { model?: File[]; scene?: File[] } = {}): Promise<EcomJob> {
  const body = new FormData()
  files.forEach((file) => body.append('files', file))
  styleFiles.forEach((file) => body.append('style_files', file))
  references.model?.forEach((file) => body.append('model_files', file))
  references.scene?.forEach((file) => body.append('scene_files', file))
  body.append('params', JSON.stringify(params))
  return request(`${BASE}/${kind}`, { method: 'POST', body })
}

/** 获取单个任务及逐图状态。 */
export function getEcomJob(id: number): Promise<EcomJob> {
  return request(`${BASE}/${id}`)
}

/** 按功能从服务端分页加载历史。 */
export function listEcomJobs(kind: string, page: number, pageSize: number): Promise<EcomJobList> {
  return request(`${BASE}?${new URLSearchParams({ kind, page: String(page), page_size: String(pageSize) })}`)
}

/** 为失败图片重新入队，服务端负责状态与余额校验。 */
export function retryEcomJobItem(id: number, idx: number): Promise<EcomJob> {
  return request(`${BASE}/${id}/items/${idx}/retry`, { method: 'POST' })
}

/** 带鉴权获取任务 zip，并沿用服务端文件名。 */
export async function downloadEcomJobZip(id: number): Promise<void> {
  const response = await requestResponse(`${BASE}/${id}/download`)
  triggerBlobDownload(await response.blob(), parseDispositionFilename(response.headers.get('Content-Disposition')) || `ecom-${id}.zip`)
}

/** 将单张成图保存到本地，跨域媒体沿用共用下载实现。 */
export async function downloadEcomJobImage(id: number, item: EcomJobItem): Promise<void> {
  if (!item.result_url) return
  const blob = await fetchMediaBlob(resolveToolMediaUrl(item.result_url))
  triggerBlobDownload(blob, `ecom-${id}-${item.idx + 1}.png`)
}

/** 从已完成图片创建同类裂变任务，费用与新出图一致。 */
export function createEcomJobVariants(id: number, idx: number, count: number): Promise<EcomJob> {
  return request(`${BASE}/${id}/items/${idx}/variants`, { method: 'POST', body: JSON.stringify({ count }) })
}
