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
}

/** 场景分页列表 */
export type EcomSceneList = {
  items: EcomScene[]
  total: number
  page: number
  page_size: number
  industries: { id: string; name: string }[]
}

// 统一错误：401 抛「未登录」，其它取后端中文短句
function request<T>(path: string, fallback: string, init?: RequestInit): Promise<T> {
  return createJsonRequester((response, detail) => {
    if (response.status === 401) throw new Error('未登录')
    throw new Error(apiErrorMessage(detail, fallback))
  })<T>(`${BASE}${path}`, init)
}

/** 场景列表（系统场景 + 我的场景，按行业筛选，服务端分页） */
export function listEcomScenes(industry: string, page = 1, pageSize = 24, signal?: AbortSignal): Promise<EcomSceneList> {
  const qs = new URLSearchParams({ industry, page: String(page), page_size: String(pageSize) })
  return request(`?${qs}`, '加载场景失败', { signal })
}
