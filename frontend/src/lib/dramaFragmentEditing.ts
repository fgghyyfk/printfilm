import type { DramaEpisode, DramaFragment } from '../api/drama'

/** 分镜列表对应的编辑版本；运行态轮询不推进此快照。 */
export type FragmentEditSnapshot = { revision: number; deletedIds: number[] }

// 与后端保持相同的服务端成片/任务字段边界，普通创作设置可提交。
const serverKeys = new Set([
  'video_versions', 'lastFrameUrl', 'last_frame_url', 'generation', 'generation_attempts',
  'aspect_ratio', 'resolution', 'video_width', 'video_height', 'reference_snapshot', 'video_input_snapshot',
])

/** 完整采用服务端分镜时建立新的编辑快照。 */
export function createFragmentEditSnapshot(episode: Pick<DramaEpisode, 'revision'>): FragmentEditSnapshot {
  return { revision: episode.revision, deletedIds: [] }
}

/** 过滤生成字段，防止复制或旧客户端参数伪造成片。 */
function editableParams(params: DramaFragment['params']) {
  return Object.fromEntries(Object.entries(params || {}).filter(([key]) => !serverKeys.has(key)))
}

/** 将当前编辑列表连同其版本和显式删除项序列化。 */
export function fragmentSaveBody(snapshot: FragmentEditSnapshot, fragments: Array<Partial<DramaFragment> & { content: string; sort_order: number }>) {
  return {
    revision: snapshot.revision,
    deleted_ids: [...snapshot.deletedIds],
    fragments: fragments.map(({ cover: _cover, video: _video, clientKey: _clientKey, ...fragment }) => ({
      ...fragment,
      params: editableParams(fragment.params),
    })),
  }
}

/** 复制创作内容与引用，新镜不继承服务端成片和生成历史。 */
export function duplicateEditableFragment(fragment: DramaFragment): DramaFragment {
  return { ...fragment, clientKey: undefined, id: 0, cover: '', video: '', params: editableParams(fragment.params) }
}

/** 同 id 合并服务端运行态，保留当前编辑内容、集合及编辑快照。 */
export function mergeFragmentRuntime(local: DramaFragment[], remote: DramaFragment[]): DramaFragment[] {
  const byId = new Map(remote.map((fragment) => [fragment.id, fragment]))
  return local.map((fragment) => {
    const current = byId.get(fragment.id)
    if (!current) return fragment
    const params = { ...(fragment.params || {}) }
    for (const key of serverKeys) {
      if (Object.hasOwn(current.params || {}, key)) params[key] = current.params![key]
      else delete params[key]
    }
    return { ...fragment, video: current.video, cover: current.cover, params }
  })
}
