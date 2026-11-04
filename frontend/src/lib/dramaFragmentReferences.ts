import type { DramaAsset, DramaFragment } from '../api/drama'

/** 引用表顺序优先，正文新增引用追加；显式优先级只重排已有成员。 */
export function orderedFragmentAssetIds(fragment: DramaFragment | null | undefined): number[] {
  if (!fragment) return []
  const mentions = Array.from((fragment.content || '').matchAll(/@asset:(\d+)/g), (match) => Number(match[1]))
  const ids = Array.from(new Set([...(fragment.asset_ids || []), ...mentions].filter((id) => id > 0)))
  const order = fragment.params?.reference_asset_order
  return Array.isArray(order)
    ? Array.from(new Set([...order.filter((id): id is number => typeof id === 'number' && ids.includes(id)), ...ids]))
    : ids
}

/** 与后端相同的视觉参考上限及顺序：一个场景、三个角色、两个道具、总计六项。 */
export function selectedFragmentAssetIds(fragment: DramaFragment | null | undefined, assets: DramaAsset[]): number[] {
  const byId = new Map(assets.map((asset) => [asset.id, asset]))
  const ids = orderedFragmentAssetIds(fragment).filter((id) => byId.has(id))
  const kind = (id: number) => (byId.get(id)?.type || '').trim().toLowerCase()
  const scenes = ids.filter((id) => kind(id) === 'scene').slice(0, 1)
  const characters = ids.filter((id) => kind(id) === 'character').slice(0, 3)
  const props = ids.filter((id) => ['prop', 'material', 'none'].includes(kind(id))).slice(0, 2)
  const other = ids.filter((id) => !['scene', 'character', 'prop', 'material', 'none'].includes(kind(id)))
  return [...scenes, ...characters, ...props, ...other].slice(0, 6)
}

/** 显示所有角色与实际被上限忽略的名字，不重复计算同一引用。 */
export function fragmentCharacterLimitWarning(fragment: DramaFragment | null | undefined, assets: DramaAsset[]): string | null {
  const byId = new Map(assets.map((asset) => [asset.id, asset]))
  const characters = orderedFragmentAssetIds(fragment).filter((id) => (byId.get(id)?.type || '').trim().toLowerCase() === 'character')
  if (characters.length <= 3) return null
  const ignored = characters.slice(3).map((id) => byId.get(id)?.name || `#${id}`)
  return `本镜引用 ${characters.length} 个角色，仅前 3 个生效；忽略：${ignored.join('、')}。可调整角色优先级。`
}

function referenceImageUrl(asset: DramaAsset): string {
  const type = (asset.type || '').toLowerCase()
  const assetType = (asset.asset_type || '').toLowerCase()
  if (['audio', 'voice'].includes(assetType) || ['audio', 'voice', 'narration'].includes(type)) return ''
  const url = assetType === 'image' || ['character', 'scene', 'prop', 'material', 'image'].includes(type)
    ? asset.cover || asset.url || '' : asset.cover || ''
  return /\.(mp3|wav|m4a|aac|ogg|flac)(?:[?#]|$)/i.test(url) ? '' : url
}

function canonicalImageMedia(url: string): string {
  // 与后端旧媒体标识一致；静态目录上传到 OSS 仅改变地址前缀。
  const generated = url.match(/(?:^|\/)generated\/[^?#]*/)
  return generated ? `/static/${generated[0].replace(/^\//, '')}` : url.trim()
}

/** 视频的真实提交输入，不用资产更新时间推测形象变化。 */
export function fragmentReferenceFreshness(fragment: DramaFragment, assets: DramaAsset[]): 'unknown' | 'fresh' | 'inputs_changed' | 'image_updated' {
  const snapshot = fragment.params?.reference_snapshot
  if (!Array.isArray(snapshot)) return 'unknown'
  const byId = new Map(assets.map((asset) => [asset.id, asset]))
  const current = selectedFragmentAssetIds(fragment, assets)
    .map((id) => byId.get(id)!)
    .filter((asset) => Boolean(referenceImageUrl(asset)))
  if (snapshot.length !== current.length || snapshot.some((row, index) => row?.asset_id !== current[index]?.id)) {
    return 'inputs_changed'
  }
  return snapshot.some((row, index) => row.revision || current[index].image_revision
    ? row.revision !== (current[index].image_revision || '')
    : canonicalImageMedia(String(row.media || '')) !== canonicalImageMedia(referenceImageUrl(current[index])))
    ? 'image_updated' : 'fresh'
}

export function fragmentReferenceStatusLabel(fragment: DramaFragment, assets: DramaAsset[]): string {
  if (!fragment.video) return ''
  switch (fragmentReferenceFreshness(fragment, assets)) {
    case 'inputs_changed': return '参考顺序已变，建议重生成'
    case 'image_updated': return '形象已更新，建议重生成'
    case 'unknown': return '来源版本未知'
    default: return ''
  }
}
