/** 电商图场景库 API：/api/tools/ecom-scenes */

import { createJsonRequester } from './client'
import { apiErrorMessage } from './http'

const BASE = '/api/tools/ecom-scenes'

/** 场景卡的结构化字段（生成时拼成提示词） */
export type EcomSceneSpec = {
  backdrop: string
  palette: string
  surface: string
  props: string[]
  light: string
  mood: string
  /** 6 位 hex，不带 # */
  bg_color: string
  product_ratio: number
  blank: 'top' | 'bottom' | 'none'
  prompt: string
}

/** 节日节点（最近一次：正在进行或即将到来） */
export type EcomFestival = {
  key: string
  name: string
  type: string
  type_name: string
  en: string
  /** ISO 日期：开始备货 / 节日当天 / 结束 */
  start: string
  date: string
  end: string
  active: boolean
  /** 距节日当天的天数（进行中的区间类节点可能为负） */
  days: number
  /** 进行中或 60 天内开始 */
  soon: boolean
}

/** 一张场景卡 */
export type EcomScene = {
  key: string
  name: string
  scope: 'system' | 'user'
  industries: string[]
  description: string
  /** 可能为空字符串，此时用 spec.bg_color 做色块占位 */
  cover_url: string
  spec: EcomSceneSpec
  /** 风格组 id（festival / natural / …，我的场景为 mine） */
  group: string
  festival: EcomFestival | null
}

export type EcomOption = { id: string; name: string }

/** 场景分页列表 */
export type EcomSceneList = {
  items: EcomScene[]
  total: number
  page: number
  page_size: number
  industries: EcomOption[]
  groups: EcomOption[]
  festival_types: EcomOption[]
}

// 统一错误：401 抛「未登录」，其它取后端中文短句
function request<T>(path: string, fallback: string, init?: RequestInit): Promise<T> {
  return createJsonRequester((response, detail) => {
    if (response.status === 401) throw new Error('未登录')
    throw new Error(apiErrorMessage(detail, fallback))
  })<T>(`${BASE}${path}`, init)
}

/** 场景列表（系统场景 + 我的场景；按行业、风格组、节日类型筛选，服务端分页） */
export function listEcomScenes(params: { industry: string; group?: string; festivalType?: string; page?: number; pageSize?: number },
  signal?: AbortSignal): Promise<EcomSceneList> {
  const qs = new URLSearchParams({
    industry: params.industry, group: params.group || '', festival_type: params.festivalType || '',
    page: String(params.page || 1), page_size: String(params.pageSize || 24),
  })
  return request(`?${qs}`, '加载场景失败', { signal })
}
