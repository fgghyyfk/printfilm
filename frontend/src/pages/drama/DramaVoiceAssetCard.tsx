/** 资产库「音色」Tab 单卡：描述编辑、试听与合成 */
import { Trash2 } from 'lucide-react'
import { resolveDramaMediaUrl, type DramaAsset } from '../../api/drama'
import { CharacterVoicePreviewButton } from '../../components/drama/CharacterVoicePreviewButton'
import { AssetCard } from '../../components/assets/AssetLibrary'

type Props = {
  asset: DramaAsset
  promptValue: string
  synthBusy: boolean
  onPromptChange: (value: string) => void
  onPromptBlur: () => void
  onSynth?: () => void
  onDelete: () => void
  onError: (message: string) => void
}

// 渲染音色资产卡片
export function DramaVoiceAssetCard({
  asset,
  promptValue,
  synthBusy,
  onPromptChange,
  onPromptBlur,
  onSynth,
  onDelete,
  onError,
}: Props) {
  const audioSrc = resolveDramaMediaUrl(asset.url)
  const hasAudio = Boolean(audioSrc)

  return (
    <AssetCard
      asset={asset}
      subtitle={`项目 #${asset.project_id}`}
      footer="项目资产"
      updatedAt={asset.updated_at}
      busy={synthBusy}
      status={synthBusy ? '合成中' : hasAudio ? '已合成' : '待合成'}
      actions={<>
        {hasAudio && <CharacterVoicePreviewButton url={asset.url || ''} label={asset.name || undefined} variant="button" size="sm" onError={onError} />}
        {onSynth && <button type="button" className="pf-btn pf-btn-lime pf-btn-sm" disabled={synthBusy || !promptValue.trim()} onClick={onSynth}>
          {synthBusy ? '合成中…' : hasAudio ? '重新合成' : '按提示词合成'}
        </button>}
        <button type="button" className="pf-btn pf-btn-sm drama-btn-danger-text" aria-label={`删除 ${asset.name || '音色'}`} disabled={synthBusy} onClick={onDelete}><Trash2 size={14} />删除</button>
      </>}
    >
        <label className="pf-library-voice-field">
          <span>音色描述</span>
          <textarea
            rows={3}
            value={promptValue}
            disabled={synthBusy}
            onChange={(e) => onPromptChange(e.target.value)}
            onBlur={onPromptBlur}
            placeholder="描述音色：年龄、性别、语气、语速…"
          />
        </label>

        {hasAudio && <audio className="pf-library-audio" controls preload="none" src={audioSrc} />}
    </AssetCard>
  )
}
