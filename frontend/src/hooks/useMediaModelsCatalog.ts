import { translate as translateUi } from '../i18n/runtime'
/** 拉取并缓存前台 TokenFree 图/视频模型目录。 */
import { useEffect, useState } from 'react'
import { api, type MediaModelOption, type MediaModelsCatalog } from '../api'
export { resolveCatalogModelSelection } from '../lib/mediaCatalogSelection'

type CatalogScope = '' | 'tools'
/* 按 scope 分别缓存：默认目录（短视频/漫剧）与图片生成页目录（可含 OpenAI 生图） */
type CacheEntry = { cached: MediaModelsCatalog | null; inflight: Promise<MediaModelsCatalog> | null; fetchedAt: number; listeners: Set<(value: MediaModelsCatalog) => void> }
const caches: Record<CatalogScope, CacheEntry> = {
  '': { cached: null, inflight: null, fetchedAt: 0, listeners: new Set() },
  tools: { cached: null, inflight: null, fetchedAt: 0, listeners: new Set() },
}

function loadCatalog(scope: CatalogScope, force = false): Promise<MediaModelsCatalog> {
  const entry = caches[scope]
  if (!force && entry.cached && Date.now() - entry.fetchedAt < 60_000) return Promise.resolve(entry.cached)
  if (!entry.inflight) {
    entry.inflight = api.mediaModels(scope).then((cat) => {
      entry.cached = cat
      entry.fetchedAt = Date.now()
      entry.listeners.forEach(listener => listener(cat))
      return cat
    }).finally(() => { entry.inflight = null })
  }
  return entry.inflight
}

/** 订阅模型目录；scope='tools' 用于图片生成页（含后台开启的 OpenAI 生图模型） */
export function useMediaModelsCatalog(scope: CatalogScope = '') {
  const [catalog, setCatalog] = useState<MediaModelsCatalog | null>(caches[scope].cached)

  useEffect(() => {
    let cancelled = false
    const entry = caches[scope]
    const receive = (value: MediaModelsCatalog) => { if (!cancelled) setCatalog(value) }
    entry.listeners.add(receive)
    const refresh = () => { void loadCatalog(scope, true).catch(() => {}) }
    const timer = setInterval(() => { void loadCatalog(scope).catch(() => {}) }, 60_000)
    window.addEventListener('focus', refresh)
    window.addEventListener('media-prices-changed', refresh)
    loadCatalog(scope)
      .then((cat) => {
        if (!cancelled) setCatalog(cat)
      })
      .catch(() => {
        if (!cancelled) setCatalog(null)
      })
    return () => {
      cancelled = true
      entry.listeners.delete(receive)
      clearInterval(timer)
      window.removeEventListener('focus', refresh)
      window.removeEventListener('media-prices-changed', refresh)
    }
  }, [scope])

  return catalog
}

/** 当前目录下的视频模型；目录未到时为空。 */
export function catalogVideoModels(catalog: MediaModelsCatalog | null): MediaModelOption[] {
  return catalog?.video_models ?? []
}

/** 当前目录下的图片模型；目录未到时为空。 */
export function catalogImageModels(catalog: MediaModelsCatalog | null): MediaModelOption[] {
  return catalog?.image_models ?? []
}

/** 用目录 label 展示模型名，找不到则显示 id。 */
export function catalogModelLabel(
  modelId: string | undefined | null,
  models: Array<{ id: string; label: string }>,
  fallback = translateUi("workflow.model"),
): string {
  const id = (modelId || '').trim()
  if (!id) return fallback
  return models.find((m) => m.id === id)?.label || id
}
