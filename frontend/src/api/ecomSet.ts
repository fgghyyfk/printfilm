/** 一图出套图 API：/api/tools/ecom-set/* */

import { throwApiError } from '../lib/apiError'
import { triggerBlobDownload } from '../lib/clientDownload'
import { parseDispositionFilename } from '../lib/ecomSet'
import { createJsonRequester, requestResponse } from './client'
import { apiErrorMessage } from './http'
import type { ApiErrorHandler } from './http'

const BASE = '/api/tools/ecom-set'

export type EcomSetItemStatus = 'planned' | 'queued' | 'running' | 'succeeded' | 'failed'
export type EcomSetStatus = 'planned' | 'generating' | 'done' | 'partial' | 'failed'

export interface EcomSetItem {
  idx: number
  slot: string
  slot_label: string
  layout: string
  ratio: string
  scene: string
  title: string
  subtitle: string
  bullets: string[]
  base_url: string | null
  final_url: string | null
  status: EcomSetItemStatus
  error: string | null
}

export interface EcomSet {
  id: number
  status: EcomSetStatus
  name: string
  platform: string
  style: string
  count: number
  product_image_url: string
  product: { name: string; category: string; colors: string[]; selling_points: string[] }
  warnings: string[]
  image_fen: number
  total_fen: number
  items: EcomSetItem[]
  /** 由这套图生成的带货视频项目（未生成为 null） */
  project_id: number | null
  created_at: string
}

export interface EcomSetOptions {
  platforms: { id: string; name: string }[]
  styles: { id: string; name: string }[]
  counts: number[]
  plan_fen: number
  image_fen: number
  billing_enabled: boolean
}

export type EcomSetList = {
  items: EcomSet[]
  total: number
  page: number
  page_size: number
}

/** 规划请求参数（multipart） */
export type EcomSetPlanPayload = {
  file: File
  name: string
  platform: string
  style: string
  count: number
  notes?: string
}

/** 方案编辑：单张可改字段 */
export type EcomSetPlanItemPatch = Pick<EcomSetItem, 'idx' | 'scene' | 'title' | 'subtitle' | 'bullets'>

/** 带货视频画幅 */
export type DaihuoRatio = '9:16' | '16:9' | '1:1' | '4:3'

/** 生成带货视频项目参数 */
export type DaihuoProjectPayload = { output_ratio: DaihuoRatio; voice_id?: string }

/** 文案编辑：单张排版文字 */
export type EcomSetCopyPatch = Pick<EcomSetItem, 'title' | 'subtitle' | 'bullets'>

// 错误策略：401 统一抛「未登录」，收费入口额外触发余额不足充值引导
function errorHandler(fallback: string, notifyBilling: boolean): ApiErrorHandler {
  return (response, detail) => {
    if (response.status === 401) throw new Error('未登录')
    if (notifyBilling) throwApiError(response.status, detail, fallback)
    throw new Error(apiErrorMessage(detail, fallback))
  }
}

// 发送 JSON（或 FormData）请求并解析结果
function requestEcom<T>(path: string, fallback: string, init?: RequestInit, notifyBilling = false): Promise<T> {
  return createJsonRequester(errorHandler(fallback, notifyBilling))<T>(`${BASE}${path}`, init)
}

// 拉取平台/风格/张数/价格等表单选项
export function getEcomSetOptions(): Promise<EcomSetOptions> {
  return requestEcom('/options', '加载选项失败')
}

// 上传商品图并生成出图方案（同步，约 10–20 秒，收规划费）
export function planEcomSet(payload: EcomSetPlanPayload): Promise<EcomSet> {
  const body = new FormData()
  body.append('file', payload.file)
  body.append('name', payload.name)
  body.append('platform', payload.platform)
  body.append('style', payload.style)
  body.append('count', String(payload.count))
  body.append('notes', payload.notes || '')
  return requestEcom('/plan', '方案生成失败', { method: 'POST', body }, true)
}

// 保存用户修改后的方案（仅 planned 可改，免费）
export function saveEcomSetPlan(id: number, items: EcomSetPlanItemPatch[]): Promise<EcomSet> {
  return requestEcom(`/${id}/plan`, '保存失败', { method: 'PUT', body: JSON.stringify({ items }) })
}

// 确认方案，批量开始生成
export function generateEcomSet(id: number): Promise<EcomSet> {
  return requestEcom(`/${id}/generate`, '提交生成失败', { method: 'POST' }, true)
}

// 查询套图详情（生成中轮询）
export function getEcomSet(id: number): Promise<EcomSet> {
  return requestEcom(`/${id}`, '加载套图失败')
}

// 我的套图列表（服务端分页，按创建时间倒序）
export function listEcomSets(page = 1, pageSize = 12): Promise<EcomSetList> {
  const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return requestEcom(`?${qs}`, '加载列表失败')
}

// 单张重新生成，可选改画面描述（收一张生图费）
export function regenerateEcomSetItem(id: number, idx: number, scene?: string): Promise<EcomSet> {
  const body = scene === undefined ? {} : { scene }
  return requestEcom(
    `/${id}/items/${idx}/regenerate`,
    '重新生成失败',
    { method: 'POST', body: JSON.stringify(body) },
    true,
  )
}

// 单张改文案并同步重新排版（免费）
export function updateEcomSetItemCopy(id: number, idx: number, copy: EcomSetCopyPatch): Promise<EcomSet> {
  return requestEcom(`/${id}/items/${idx}/copy`, '修改文案失败', {
    method: 'PUT',
    body: JSON.stringify(copy),
  })
}

// 带鉴权下载套图 zip 并触发浏览器保存
export async function downloadEcomSetZip(id: number, fallbackName: string): Promise<void> {
  const response = await requestResponse(`${BASE}/${id}/download`, undefined, errorHandler('下载失败', false))
  const blob = await response.blob()
  const filename = parseDispositionFilename(response.headers.get('Content-Disposition')) || fallbackName
  triggerBlobDownload(blob, filename)
}

// 用套图建带货视频项目（同一套图重复调用返回同一项目）
export function createDaihuoProject(id: number, payload: DaihuoProjectPayload): Promise<{ project_id: number }> {
  return requestEcom(`/${id}/project`, '创建带货视频失败', { method: 'POST', body: JSON.stringify(payload) }, true)
}
