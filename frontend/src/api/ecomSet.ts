/** 一图出套图 API：/api/tools/ecom-set/* */

import { throwApiError } from '../lib/apiError'
import { triggerBlobDownload } from '../lib/clientDownload'
import { parseDispositionFilename } from '../lib/ecomSet'
import { createJsonRequester, requestResponse } from './client'
import { apiErrorMessage } from './http'
import type { ApiErrorHandler } from './http'
import { appendDims, type EcomDims } from './ecomProducts'

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
  /** 标题上方的英文小标（可空） */
  en?: string
  /** 本张镜头（构图）与画面元素 */
  shot?: string
  shot_name?: string
  elements?: { key: string; name: string }[]
  base_url: string | null
  final_url: string | null
  status: EcomSetItemStatus
  error: string | null
  /** 是否调模型收费（丝巾尺寸图、平铺图为 false） */
  billable?: boolean
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
  /** general 通用商品；scarf 丝巾详情页 */
  category?: string
  styled?: EcomStyledInfo | null
  scarf?: { size: string; size_label: string; material?: string; model_asset_id: number | null; model_name: string; model_url: string } | null
}

export interface EcomSetOptions {
  platforms: { id: string; name: string }[]
  styles: { id: string; name: string }[]
  counts: number[]
  categories?: { id: string; name: string }[]
  scarf_sizes?: { id: string; name: string }[]
  scarf_counts?: number[]
  scarf_materials?: { id: string; name: string }[]
  styled?: EcomStyledCategory[]
  frames?: { id: string; name: string }[]
  /** 场景库行业标签 */
  industries?: { id: string; name: string }[]
  /** 品类默认打开的行业，如 goods → daily */
  category_industry?: Record<string, string>
  /** 字体风格：serif 典雅衬线 / sans 简约黑体 / bold 促销醒目 */
  type_styles?: { id: string; name: string; hint: string }[]
  /** 出图模型（GPT Image 2 仅在后台开通后出现），带各自单价（分） */
  image_engines?: { id: string; name: string; hint: string; image_fen: number }[]
  /** 镜头库 */
  shots?: { key: string; name: string }[]
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
/** 「模特上身」套图信息（丝巾 / 服饰 / 首饰 / 包袋） */
export type EcomStyledInfo = {
  category: string
  size: string
  size_label: string
  material: string
  material_label?: string
  frame?: string
  model_asset_id: number | null
  model_name: string
  model_url: string
  product_id?: number | null
  color_index?: number
  color_name?: string
  fabric_url?: string
  real_url?: string
  /** 场景库场景（未指定为空） */
  scene_key?: string | null
  scene_name?: string | null
  /** 当前字体风格 */
  type_style?: string
  /** 出图模型 */
  image_engine?: string
  /** 本套元素池（单张可从中改选） */
  elements?: { key: string; name: string; image_url: string }[]
  /** 商品实际尺寸（厘米） */
  dims?: EcomDims
  /** 成图不加文字 */
  no_text?: boolean
}

/** 品类选项（来自 /options 的 styled） */
export type EcomStyledCategory = {
  id: string
  name: string
  materials: { id: string; name: string }[]
  sizes: { id: string; name: string }[]
  default_material: string
  default_size: string
  counts: number[]
  default_count: number
  compositions: Record<string, string[]>
  slots: { key: string; label: string; ref: 'model' | 'product' | 'flat' | 'composite' | 'texture' | 'info' | 'hand'; free: boolean }[]
  /** 是否需要模特（goods 日化标品为 false，跳过选模特） */
  uses_model?: boolean
  /** 材质栏标签：goods 为「包装形态」，tool 为「工具形态」，其余为「材质」 */
  material_label?: string
  /** 是否走抠图原样合成（日化标品、电动工具） */
  composite?: boolean
}

export type EcomSetPlanPayload = {
  file?: File | null
  name: string
  platform: string
  style: string
  count: number
  notes?: string
  /** 丝巾详情页：品类、尺寸与模特（留空用默认模特） */
  category?: string
  scarfSize?: string
  scarfMaterial?: string
  modelAssetId?: number | null
  /** 产品化流程：画幅、勾选槽位、商品库商品与颜色款、面料实拍 */
  frame?: string
  slots?: string[]
  productId?: number | null
  colorIndex?: number
  fabric?: File | null
  /** 出图模型：seedream（默认）| gpt-image-2 */
  imageEngine?: string
  /** 画面元素 key（最多 12 个；不传自动推荐） */
  elementKeys?: string[]
  /** 场景库场景 key；留空 = 按品类默认画面 */
  sceneKey?: string
  /** 商品实际尺寸（厘米），留空用商品库里的 */
  dims?: EcomDims
  /** 成图不加文字（纯画面） */
  noText?: boolean
}

/** 方案编辑：单张可改字段 */
export type EcomSetPlanItemPatch = Pick<EcomSetItem, 'idx' | 'scene' | 'title' | 'subtitle' | 'bullets' | 'en'> & {
  shot?: string
  elements?: string[]
}

/** 带货视频画幅 */
export type DaihuoRatio = '9:16' | '16:9' | '1:1' | '4:3'

/** 生成带货视频项目参数 */
export type DaihuoProjectPayload = { output_ratio: DaihuoRatio; voice_id?: string }

/** 文案编辑：单张排版文字 */
export type EcomSetCopyPatch = Pick<EcomSetItem, 'title' | 'subtitle' | 'bullets' | 'en'>

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
  if (payload.file) body.append('file', payload.file)
  body.append('name', payload.name)
  body.append('platform', payload.platform)
  body.append('style', payload.style)
  body.append('count', String(payload.count))
  body.append('notes', payload.notes || '')
  body.append('category', payload.category || 'general')
  if (payload.category && payload.category !== 'general') {
    body.append('scarf_size', payload.scarfSize || '')
    body.append('scarf_material', payload.scarfMaterial || '')
    if (payload.modelAssetId) body.append('model_asset_id', String(payload.modelAssetId))
  }
  if (payload.frame) body.append('frame', payload.frame)
  if (payload.slots?.length) body.append('slots', payload.slots.join(','))
  if (payload.productId) {
    body.append('product_id', String(payload.productId))
    body.append('color_index', String(payload.colorIndex || 0))
  }
  if (payload.fabric) body.append('fabric', payload.fabric)
  if (payload.sceneKey) body.append('scene_key', payload.sceneKey)
  if (payload.imageEngine) body.append('image_engine', payload.imageEngine)
  if (payload.elementKeys?.length) body.append('element_keys', payload.elementKeys.join(','))
  appendDims(body, payload.dims)
  if (payload.noText) body.append('no_text', 'true')
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
export function regenerateEcomSetItem(id: number, idx: number, scene?: string,
  layout?: { shot?: string; elements?: string[] }): Promise<EcomSet> {
  const body = { ...(scene === undefined ? {} : { scene }), ...(layout || {}) }
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

// 调整套图顺序（order 为现有序号按新顺序排列；免费，长图与 zip 跟着变）
export function reorderEcomSet(id: number, order: number[]): Promise<EcomSet> {
  return requestEcom(`/${id}/order`, '调整顺序失败', { method: 'PUT', body: JSON.stringify({ order }) })
}

// 带鉴权下载套图 zip 并触发浏览器保存
export async function downloadEcomSetZip(id: number, fallbackName: string): Promise<void> {
  const response = await requestResponse(`${BASE}/${id}/download`, undefined, errorHandler('下载失败', false))
  const blob = await response.blob()
  const filename = parseDispositionFilename(response.headers.get('Content-Disposition')) || fallbackName
  triggerBlobDownload(blob, filename)
}

// 下载详情长图（已完成的图按顺序竖向拼接）
export async function downloadEcomSetLong(id: number, fallbackName: string): Promise<void> {
  const response = await requestResponse(`${BASE}/${id}/long`, undefined, errorHandler('下载长图失败', false))
  const blob = await response.blob()
  triggerBlobDownload(blob, parseDispositionFilename(response.headers.get('Content-Disposition')) || fallbackName)
}

// 同款换色：沿用方案换另一个颜色款，返回新建的待生成套图
export function recolorEcomSet(id: number, colorIndex: number): Promise<EcomSet> {
  return requestEcom(`/${id}/recolor`, '换色失败', { method: 'POST', body: JSON.stringify({ color_index: colorIndex }) })
}

// 用套图建带货视频项目（同一套图重复调用返回同一项目）
export function createDaihuoProject(id: number, payload: DaihuoProjectPayload): Promise<{ project_id: number }> {
  return requestEcom(`/${id}/project`, '创建带货视频失败', { method: 'POST', body: JSON.stringify(payload) }, true)
}

/** 切换整套图的字体风格（已出图的全部重新排字，免费） */
export function setEcomSetTypeStyle(id: number, typeStyle: string): Promise<EcomSet> {
  return requestEcom(`/${id}/type-style`, '切换字体失败', { method: 'PUT', body: JSON.stringify({ type_style: typeStyle }) })
}

/** 往本套元素池追加元素（单张换元素前用） */
export function addEcomSetElements(id: number, keys: string[]): Promise<EcomSet> {
  return requestEcom(`/${id}/elements`, '添加元素失败', { method: 'POST', body: JSON.stringify({ keys }) })
}
