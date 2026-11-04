import { translate as translateUi } from '../i18n/runtime'
/** 漫剧资产是否已有形象图（AI 生成或本地上传；SVG 占位不算） */
import type { DramaAsset } from '../api/drama'

const BITMAP_SUFFIXES = ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp']

// 是否为可提交 Seedance 的位图 URL（排除 svg 占位）
function isBitmapImageUrl(url: string): boolean {
  const path = url.trim().split('?', 1)[0].toLowerCase()
  if (!path) return false
  return BITMAP_SUFFIXES.some((suffix) => path.endsWith(suffix))
}

// 与后端参考图判定一致；临时预览存在不代表可用于视频生成。
export function dramaAssetHasReferenceImage(asset: DramaAsset): boolean {
  const cover = (asset.cover || '').trim()
  const url = (asset.url || '').trim()
  return isBitmapImageUrl(cover || url)
}

// 从 params 中读取可能存在的预览 URL（画布同步等）
function readParamsMediaUrl(asset: DramaAsset): string {
  const params = asset.params
  if (!params || typeof params !== 'object') return ''
  const record = params as Record<string, unknown>
  for (const key of ['mediaUrl', 'previewUrl', 'imageUrl']) {
    const value = String(record[key] || '').trim()
    if (value) return value
  }
  const gen = record.generation
  if (gen && typeof gen === 'object') {
    const g = gen as Record<string, unknown>
    for (const key of ['cover', 'url', 'imageUrl']) {
      const value = String(g[key] || '').trim()
      if (value) return value
    }
  }
  return ''
}

// 资产是否已有有效形象图（生成或上传的位图）
export function dramaAssetHasImage(asset: DramaAsset): boolean {
  const cover = (asset.cover || '').trim()
  const url = (asset.url || '').trim()
  const candidate = cover || url || readParamsMediaUrl(asset)
  if (!candidate) return false
  // 无扩展名的 OSS/静态路径仍视为有效；仅明确 .svg 占位算缺图
  if (candidate.toLowerCase().includes('.svg')) return false
  if (BITMAP_SUFFIXES.some((s) => candidate.toLowerCase().split('?', 1)[0].endsWith(s))) return true
  // data:image 或远端无后缀 URL 也算有图
  if (candidate.startsWith('data:image/')) return true
  if (candidate.startsWith('http://') || candidate.startsWith('https://') || candidate.startsWith('/')) {
    return !candidate.toLowerCase().includes('.svg')
  }
  return isBitmapImageUrl(candidate)
}

// 是否仍需生图（无图且非纯音频类资产时可批量入队）
export function dramaAssetNeedsImageGeneration(asset: DramaAsset): boolean {
  const kind = (asset.type || '').toLowerCase()
  if (['voice', 'video', 'audio', 'text'].includes(kind)) return false
  return !dramaAssetHasReferenceImage(asset)
}

// 卡片/弹窗生图按钮文案（queueLabel 为排队态文案，有值时优先）
export function dramaAssetImageGenButtonLabel(
  asset: DramaAsset,
  queueLabel: string | null,
): string {
  if (queueLabel) return queueLabel
  return dramaAssetHasImage(asset) ? translateUi("workflow.regenerateImage") : translateUi("workflow.generateImage")
}
