import { useI18n } from '../../i18n'
import { translate as translateUi } from '../../i18n/runtime'
/** 分集编辑：左侧资产栏（本集/全集 + 分类卡片） */
import { type DramaAsset } from '../../api/drama'
import { AssetCard, AssetEmpty, AssetGrid, AssetTypeFilter } from '../../components/assets/AssetLibrary'
import { CharacterVoicePreviewButton } from '../../components/drama/CharacterVoicePreviewButton'
import { readAssetVoiceBinding } from './CharacterVoiceBindModal'
import { useCharacterVoiceGenerationEnabled } from '../../hooks/usePublicCapabilities'
import {
  ASSET_TABS,
  normalizeAssetTab,
  type AssetScope,
  type AssetTab,
} from './dramaEpisodeEditUtils'

type Props = {
  readOnly?: boolean
  scope: AssetScope
  tab: AssetTab | null
  assets: DramaAsset[]
  activeIds?: Set<number>
  imageBusyIds?: ReadonlySet<number>
  createBusy?: boolean
  onScopeChange: (scope: AssetScope) => void
  onTabChange: (tab: AssetTab | null) => void
  onOpenCanvas: () => void
  /** 打开资产详情：编辑提示词 / 重新生成 / 上传 */
  onOpenAsset: (asset: DramaAsset) => void
  /** 插入 @asset 到当前分镜脚本 */
  onMention: (asset: DramaAsset) => void
  /** 取消当前分镜对该资产的关联（不删除资产） */
  onUnlinkAsset?: (assetId: number) => void
  onGenerateVoice?: (asset: DramaAsset) => void
  voiceBusyIds?: ReadonlySet<number>
  onVoiceError?: (message: string) => void
  /** 自定义新建当前分类资产 */
  onCreateAsset?: () => void
  /** 从全局资产库导入 */
  onImportAsset?: () => void
}

// 当前分类新建按钮文案
function createLabel(tab: AssetTab | null): string {
  if (tab === 'scene') return translateUi("workflow.newScene")
  if (tab === 'prop') return translateUi("workflow.newProp")
  return translateUi("workflow.newCharacter")
}

// 渲染分集编辑左侧资产栏
export function EpisodeEditAssetPanel({
  readOnly = false,
  scope,
  tab,
  assets,
  activeIds,
  imageBusyIds,
  createBusy,
  onScopeChange,
  onTabChange,
  onOpenCanvas,
  onOpenAsset,
  onMention,
  onUnlinkAsset,
  onGenerateVoice,
  voiceBusyIds,
  onVoiceError,
  onCreateAsset,
  onImportAsset,
}: Props) {
  const { t: translateUi } = useI18n()
  const voiceGenerationEnabled = useCharacterVoiceGenerationEnabled()
  return (
    <aside className="drama-ep-assets">
      <div className="drama-ep-assets-top">
        <div className="drama-ep-scope">
          <button
            type="button"
            className={scope === 'episode' ? 'active' : ''}
            onClick={() => onScopeChange('episode')}
          >
            {translateUi("workflow.thisEpisode")}
          </button>
          <button
            type="button"
            className={scope === 'series' ? 'active' : ''}
            onClick={() => onScopeChange('series')}
          >
            {translateUi("workflow.allEpisodes")}
          </button>
        </div>
        <button
          type="button"
          className="drama-ep-icon-btn solid"
          aria-label={translateUi("workflow.openStoryboardCanvas")}
          onClick={onOpenCanvas}
        >
          +
        </button>
      </div>
      <div className="drama-ep-asset-filters"><AssetTypeFilter options={ASSET_TABS.map((item) => ({ value: item.key, label: item.label }))} value={tab} onChange={(value) => onTabChange(tab === value ? null : value)} /></div>
      {onCreateAsset || onImportAsset ? (
        <div className="drama-ep-asset-actions">
          {onCreateAsset ? (
            <button
              type="button"
              className="drama-ep-asset-action-btn"
              disabled={readOnly || createBusy}
              onClick={onCreateAsset}
            >
              {createBusy ? translateUi("workflow.creating") : createLabel(tab)}
            </button>
          ) : null}
          {onImportAsset ? (
            <button
              type="button"
              className="drama-ep-asset-action-btn is-ghost"
              disabled={readOnly || createBusy}
              onClick={onImportAsset}
            >
              {translateUi("workflow.import")}
            </button>
          ) : null}
        </div>
      ) : null}
      <div className="drama-ep-asset-list"><AssetGrid compact>
        {assets.length === 0 ? (
          <AssetEmpty>
            {scope === 'episode'
              ? '本集暂无引用资产，可点上方「新建」加入，或切换「全集」查看项目资产'
              : '暂无资产，可点上方「新建 / 导入」添加'}
          </AssetEmpty>
        ) : (
          assets.map((asset) => {
            const isCharacter = normalizeAssetTab(asset.type) === 'character'
            const voice = isCharacter ? readAssetVoiceBinding(asset) : null
            const isActive = activeIds?.has(asset.id)
            const voiceGenerating = voiceBusyIds?.has(asset.id) ?? false
            const imageBusy = imageBusyIds?.has(asset.id) ?? false
            return (
              <AssetCard
                key={asset.id}
                asset={asset}
                compact
                busy={imageBusy}
                selected={Boolean(isActive)}
                status={imageBusy ? '生成中' : isActive ? '已关联' : undefined}
                subtitle={voice ? '已绑定音色' : undefined}
                onOpen={() => onOpenAsset(asset)}
                actions={<>
                  {isActive && onUnlinkAsset ? (
                    <button
                      type="button"
                      className="drama-ep-asset-op-btn"
                      disabled={readOnly}
                      onClick={() => onUnlinkAsset(asset.id)}
                      title={translateUi("workflow.removeThisShotSReferenceWithoutDeletingTheAsset")}
                    >
                      {translateUi("workflow.unlink")}
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="drama-ep-asset-op-btn"
                      disabled={readOnly}
                      onClick={() => onMention(asset)}
                      title={translateUi("workflow.insertIntoThisShotSScript")}
                    >
                      {translateUi("workflow.insert")}
                    </button>
                  )}
                  {isCharacter ? (
                    voice ? (
                      <CharacterVoicePreviewButton
                        url={voice.url}
                        label={voice.label}
                        variant="inline"
                        className="drama-ep-asset-voice-btn is-bound"
                        onError={onVoiceError}
                      />
                    ) : voiceGenerationEnabled && onGenerateVoice ? (
                      <button
                        type="button"
                        className="drama-ep-asset-voice-btn"
                        disabled={readOnly || voiceGenerating}
                        onClick={() => onGenerateVoice(asset)}
                      >
                        {voiceGenerating ? translateUi("workflow.generating") : translateUi("workflow.generateVoice")}
                      </button>
                    ) : null
                  ) : null}
                </>}
              />
            )
          })
        )}
      </AssetGrid></div>
    </aside>
  )
}
