import { AudioLines, Image, Video } from 'lucide-react'
import { AssetTypeFilter } from './AssetLibrary'

export type MediaLibraryFilter = {
  kind: 'image' | 'video' | 'audio'
  imageType: 'all' | 'character' | 'scene' | 'prop'
}

const MEDIA_TYPES = [
  { value: 'image', label: '图片', icon: Image },
  { value: 'video', label: '视频', icon: Video },
  { value: 'audio', label: '音频', icon: AudioLines },
] as const
const IMAGE_TYPES = [
  { value: 'all', label: '全部图片' },
  { value: 'character', label: '角色' },
  { value: 'scene', label: '场景' },
  { value: 'prop', label: '道具' },
] as const

/** 先选媒体大类，业务分类仅用于图片；切换大类时清空图片分类。 */
export default function MediaLibraryFilters({ value, onChange }: {
  value: MediaLibraryFilter
  onChange: (value: MediaLibraryFilter) => void
}) {
  return <div className="pf-library-filter-stack">
    <div className="pf-library-media-tabs" role="group" aria-label="按媒体大类筛选">
      {MEDIA_TYPES.map(({ value: kind, label, icon: Icon }) => <button key={kind} type="button"
        className={`pf-library-pill${value.kind === kind ? ' is-active' : ''}`}
        aria-pressed={value.kind === kind}
        onClick={() => { if (value.kind !== kind) onChange({ kind, imageType: 'all' }) }}>
        <Icon size={17} aria-hidden />{label}
      </button>)}
    </div>
    {value.kind === 'image' && <div className="pf-library-image-categories">
      <span>图片分类</span>
      <AssetTypeFilter options={IMAGE_TYPES} value={value.imageType} ariaLabel="按图片分类筛选"
        onChange={imageType => onChange({ kind: 'image', imageType })} />
    </div>}
  </div>
}
