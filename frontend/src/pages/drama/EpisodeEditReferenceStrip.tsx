/** 分镜顶部：已关联资产缩略图条 */
import type { FragmentRefStripItem } from './dramaEpisodeEditUtils'
import { DRAMA_VOICE_BINDING_ENABLED } from '../../lib/dramaVoiceBinding'

type Props = {
  items: FragmentRefStripItem[]
  onSelect?: (assetId: number) => void
  onMoveCharacter?: (assetId: number, direction: -1 | 1) => void
  disabled?: boolean
}

// 渲染当前分镜关联资产条
export function EpisodeEditReferenceStrip({ items, onSelect, onMoveCharacter, disabled }: Props) {
  const characters = items.filter((item) => item.type.trim().toLowerCase() === 'character')
  if (items.length === 0) {
    return (
      <div className="drama-ep-ref-strip is-empty">
        <span className="drama-ep-ref-strip-hint">暂无关联资产 · 点击左侧卡片或键入 @asset:id</span>
      </div>
    )
  }

  return (
    <div className="drama-ep-ref-strip" aria-label="本镜关联资产">
      {items.map((item) => (
        <span key={item.assetId} className="drama-ep-ref-item">
        <button
          type="button"
          className={`drama-ep-ref-chip${item.isCharacter ? ' is-character' : ''}${
            item.voiceUrl ? ' has-voice' : item.isCharacter ? ' no-voice' : ''
          }`}
          title={`${item.name}${item.type ? ` · ${item.type}` : ''}${
            DRAMA_VOICE_BINDING_ENABLED && item.isCharacter
              ? item.voiceLabel
                ? ` · 音色：${item.voiceLabel}`
                : ' · 未绑定音色'
              : ''
          }`}
          onClick={() => onSelect?.(item.assetId)}
        >
          {item.previewUrl ? (
            <img src={item.previewUrl} alt="" draggable={false} />
          ) : (
            <span className="drama-ep-ref-chip-fallback">{(item.name || '?')[0]}</span>
          )}
          {DRAMA_VOICE_BINDING_ENABLED && item.isCharacter ? (
            <span className={`drama-ep-ref-voice-badge${item.voiceUrl ? ' bound' : ''}`}>
              {item.voiceUrl ? '音' : '无音'}
            </span>
          ) : null}
          <em>{item.name}</em>
        </button>
          {item.type.trim().toLowerCase() === 'character' && onMoveCharacter && characters.length > 1 ? (
            <span className="drama-ep-ref-priority" aria-label={`${item.name}的角色优先级`}>
              <button type="button" disabled={disabled || characters[0]?.assetId === item.assetId}
                title="提高角色优先级" aria-label={`提高${item.name}的优先级`}
                onClick={() => onMoveCharacter(item.assetId, -1)}>↑</button>
              <button type="button" disabled={disabled || characters.at(-1)?.assetId === item.assetId}
                title="降低角色优先级" aria-label={`降低${item.name}的优先级`}
                onClick={() => onMoveCharacter(item.assetId, 1)}>↓</button>
            </span>
          ) : null}
        </span>
      ))}
    </div>
  )
}
