import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { listToolReferenceAssets, type ToolReferenceAsset } from '../api/tools'

/** 解析 ?ref_assets=2,3 为资产 id 列表（去重保序） */
export function parseRefAssetIds(raw: string | null): number[] {
  const ids: number[] = []
  for (const part of (raw || '').split(',')) {
    const id = Number(part.trim())
    if (Number.isInteger(id) && id > 0 && !ids.includes(id)) ids.push(id)
  }
  return ids
}

/**
 * 图片生成页：从资产库选择的参考图（与上传文件合计受模型参考上限约束）。
 * 支持 /tools/image?ref_assets=2,3 预选资产，便于「固定模特 + 商品」快速续生。
 */
export function useImageReferenceAssets(enabled: boolean) {
  const [searchParams] = useSearchParams()
  const [assets, setAssets] = useState<ToolReferenceAsset[]>([])
  const preset = searchParams.get('ref_assets')

  // 链接带 ref_assets 时，从个人图片资产里取出对应项预选
  useEffect(() => {
    const ids = parseRefAssetIds(preset)
    if (!enabled || !ids.length) return
    const controller = new AbortController()
    void listToolReferenceAssets({ kind: 'image', page: 1, pageSize: 50 }, controller.signal)
      .then(result => setAssets(ids.map(id => result.items.find(item => item.id === id)).filter(Boolean) as ToolReferenceAsset[]))
      .catch(() => undefined)
    return () => controller.abort()
  }, [enabled, preset])

  /** 追加资产（按 id 去重） */
  function add(items: ToolReferenceAsset[]) {
    setAssets(previous => [...previous, ...items.filter(item => !previous.some(old => old.id === item.id))])
  }

  /** 移除单个资产 */
  function remove(id: number) {
    setAssets(previous => previous.filter(item => item.id !== id))
  }

  const references = useMemo(() => assets.map(item => ({ role: 'reference_image' as const, asset_id: item.id })), [assets])
  // 链接里的 color_ref=资产 id：预选为自动校色的色板
  const presetColorAssetId = parseRefAssetIds(searchParams.get('color_ref'))[0] ?? null
  return { assets, add, remove, references, presetColorAssetId }
}
