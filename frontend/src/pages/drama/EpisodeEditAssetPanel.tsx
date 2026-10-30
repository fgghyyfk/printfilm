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
  if (tab === 'scene') return '新建场景'
  if (tab === 'prop') return '新建道具'
  return '新建角色'
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
            本集
          </button>
          <button
            type="button"
            className={scope === 'series' ? 'active' : ''}
            onClick={() => onScopeChange('series')}
          >
            全集
          </button>
        </div>
        <button
          type="button"
          className="drama-ep-icon-btn solid"
          aria-label="打开分镜故事板画布"
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
              {createBusy ? '创建中…' : createLabel(tab)}
            </button>
          ) : null}
          {onImportAsset ? (
            <button
              type="button"
              className="drama-ep-asset-action-btn is-ghost"
              disabled={readOnly || createBusy}
              onClick={onImportAsset}
            >
              导入
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
                      title="仅取消当前分镜关联，不会删除资产"
                    >
                      取消关联
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="drama-ep-asset-op-btn"
                      disabled={readOnly}
                      onClick={() => onMention(asset)}
                      title="插入到当前分镜脚本"
                    >
                      插入
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
                        {voiceGenerating ? '生成中…' : '生成音色'}
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
