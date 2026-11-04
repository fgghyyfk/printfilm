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
