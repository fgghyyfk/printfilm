import { translate as translateUi } from '../../i18n/runtime'
/** 漫剧分集左侧目录（大纲 / 分集页 / 分镜页共用） */
import type { ReactNode } from 'react'
import type { DramaEpisode } from '../../api/drama'

export type DramaEpisodeDirItem = {
  id: number
  label: string
  title: string
  meta?: string
}

type DramaEpisodeDirProps = {
  title?: string
  items: DramaEpisodeDirItem[]
  activeId: number | null
  onSelect: (id: number) => void
  footer?: ReactNode
  emptyText?: string
}

// 从分集列表生成目录项（按集号排序；缺集号不伪装成第 1 集）
export function buildEpisodeDirItems(episodes: DramaEpisode[]): DramaEpisodeDirItem[] {
  const sorted = [...episodes].sort((a, b) => {
    const an = Number(a.params?.episodeNumber) || 0
    const bn = Number(b.params?.episodeNumber) || 0
    if (an !== bn) return an - bn
    return a.id - b.id
  })
  return sorted.map((ep) => {
    const epNo = Number(ep.params?.episodeNumber) || 0
    const fragCount = (ep.fragments || []).length
    return {
      id: ep.id,
      label: epNo >= 1 ? translateUi("workflow.episode", { v1: String(epNo) }) : translateUi("workflow.unnumbered", { v1: String(ep.id) }),
      title: ep.name || translateUi("workflow.episode_ebaa6d", { v1: String(ep.id) }),
      meta: fragCount > 0 ? translateUi("workflow.shotCount", { v1: String(fragCount) }) : undefined,
    }
  })
}

// 左侧分集目录
export function DramaEpisodeDir({
  title = translateUi("workflow.episodes_4c659c"),
  items,
  activeId,
  onSelect,
  footer,
  emptyText = translateUi("workflow.noEpisodesYet"),
}: DramaEpisodeDirProps) {
  return (
    <aside className="drama-episode-dir">
      <div className="drama-episode-dir-head">
        <h3>{title}</h3>
        {footer}
      </div>
      {items.length === 0 ? (
        <p className="drama-episode-dir-empty">{emptyText}</p>
      ) : (
        <ul>
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                className={activeId === item.id ? 'active' : ''}
                onClick={() => onSelect(item.id)}
              >
                <span>{item.label}</span>
                <small>
                  {item.title}
                  {item.meta ? ` · ${item.meta}` : ''}
                </small>
              </button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  )
}
