import { useI18n } from '../../i18n'
/** 分集编辑顶栏：画幅/清晰度 / 视频风格 / 字幕 / 人物介绍 / 模型 / 镜间衔接 */

import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type MouseEvent } from 'react'

import { createPortal } from 'react-dom'

import { BarChart3, ChevronDown, CircleHelp, Link2, Smile, Type, Users } from 'lucide-react'

import { DramaImageStylePreviewImg } from '../../components/drama/DramaImageStylePreviewImg'

import { DramaMediaModelPicker } from '../../components/drama/DramaMediaModelPicker'
import { SeedanceRulesModal } from '../../components/drama/SeedanceRulesModal'

import {
  getImageStyleLabel,
  IMAGE_STYLE_OPTIONS,
  type ImageStyleId,
} from '../../lib/dramaImageStyles'

import { subtitleModeUsesModelOutput, type DramaSubtitleMode } from '../../lib/dramaSubtitleSettings'
import {
  characterIntroModeEnabled,
  type DramaCharacterIntroMode,
} from '../../lib/dramaCharacterIntro'

import {
  catalogModelLabel,
  catalogVideoModels,
  useMediaModelsCatalog,
} from '../../hooks/useMediaModelsCatalog'

import { DramaProjectOutputSettings } from './DramaProjectOutputSettings'

import './canvas/nodes/dramaImageGenOptions.css'
type Props = {
  styleId: ImageStyleId | ''
  modelId: string
  episodeParams: Record<string, unknown>
  projectParams: Record<string, unknown>
  linkLastFrame: boolean
  subtitleMode: DramaSubtitleMode
  characterIntroMode: DramaCharacterIntroMode
  onStyleChange: (id: ImageStyleId | '') => void
  onModelChange: (id: string) => void
  onEpisodeOutputChange: (
    nextParams: Record<string, unknown>,
    opts?: { quiet?: boolean },
  ) => void | Promise<void>
  onLinkLastFrameChange: (enabled: boolean) => void
  onSubtitleModeChange: (mode: DramaSubtitleMode) => void
  onCharacterIntroModeChange: (mode: DramaCharacterIntroMode) => void
  disabled?: boolean
  /** 风格/字幕/介绍/衔接为项目全局，分镜页只展示不可改；画幅/清晰度仍可改（随模型过滤） */
  globalSettingsReadOnly?: boolean
}

type OpenPanel = 'style' | 'model' | 'link' | 'subtitle' | 'intro' | null

const STYLE_PANEL_WIDTH = 420

// 渲染顶栏生成参数控件
export function EpisodeEditHeaderControls({
  styleId,
  modelId,
  episodeParams,
  projectParams,
  linkLastFrame,
  subtitleMode,
  characterIntroMode,
  onStyleChange,
  onModelChange,
  onEpisodeOutputChange,
  onLinkLastFrameChange,
  onSubtitleModeChange,
  onCharacterIntroModeChange,
  disabled = false,
  globalSettingsReadOnly = false,
}: Props) {
  const { t: translateUi } = useI18n()
  const rootRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState<OpenPanel>(null)
  const [rulesOpen, setRulesOpen] = useState(false)
  const [panelStyle, setPanelStyle] = useState<CSSProperties | null>(null)
  const catalog = useMediaModelsCatalog()
  const videoModels = catalogVideoModels(catalog)
  useLayoutEffect(() => {
    if (!open || !rootRef.current) {
      setPanelStyle(null)
      return
    }
    // 固定定位下拉面板，避免与 top/bottom 冲突导致高度被压扁
    function updatePanelPosition() {
      const root = rootRef.current
      if (!root) return
      const rect = root.getBoundingClientRect()
      const width =
        open === 'style'
          ? Math.min(STYLE_PANEL_WIDTH, window.innerWidth - 24)
          : open === 'model'
            ? Math.min(400, window.innerWidth - 24)
            : Math.min(360, window.innerWidth - 24)
      let left = rect.right - width
      left = Math.max(12, Math.min(left, window.innerWidth - width - 12))
      const top = Math.min(rect.bottom + 8, window.innerHeight - 24)
      setPanelStyle({
        position: 'fixed',
        top,
        left,
        width,
        bottom: 'auto',
        right: 'auto',
        zIndex: 320,
      })
    }
    updatePanelPosition()
    window.addEventListener('resize', updatePanelPosition)
    window.addEventListener('scroll', updatePanelPosition, true)
    return () => {
      window.removeEventListener('resize', updatePanelPosition)
      window.removeEventListener('scroll', updatePanelPosition, true)
    }
  }, [open])
  useEffect(() => {
    if (!open) return
    function onDoc(e: Event) {
      const target = e.target as Node | null
      if (!target) return
      if (rootRef.current?.contains(target)) return
      if ((target as Element).closest?.('.fc-gen-opt-panel--portal')) return
      setOpen(null)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])
  const stop = (e: MouseEvent) => {
    e.stopPropagation()
  }
  const styleLabel = getImageStyleLabel(styleId) || translateUi("workflow.videoStyle")
  const modelLabel = catalogModelLabel(modelId, videoModels, translateUi("workflow.videoModel"))
  const subtitleLabel = subtitleMode === 'model' ? translateUi("workflow.modelGeneratedSubtitles") : translateUi("workflow.postProductionSubtitles")
  const subtitleUsesModel = subtitleModeUsesModelOutput(subtitleMode)
  const introLabel = characterIntroMode === 'model' ? translateUi("workflow.characterIntroductions") : translateUi("workflow.noIntroductionOverlays")
  const introEnabled = characterIntroModeEnabled(characterIntroMode)
  const globalLocked = disabled || globalSettingsReadOnly
  return (
    <div
      ref={rootRef}
      className="fc-gen-opts drama-ep-header-gen-opts"
      onMouseDown={stop}
      onPointerDown={stop}
    >
      <div className="fc-gen-opts-triggers">
        <DramaProjectOutputSettings
          scope="episode"
          params={episodeParams}
          fallbackParams={projectParams}
          disabled={disabled}
          compact
          videoModelId={modelId}
          onChange={onEpisodeOutputChange}
        />
        <button
          type="button"
          className={`fc-gen-opt-btn${open === 'style' || styleId ? ' active' : ''}${globalSettingsReadOnly ? ' is-readonly' : ''}`}
          disabled={globalLocked}
          onClick={() => {
            if (globalSettingsReadOnly) return
            setOpen((c) => (c === 'style' ? null : 'style'))
          }}
          title={globalSettingsReadOnly ? translateUi("workflow.projectSettingReadOnlyHere", { v1: String(styleLabel) }) : styleLabel}
        >
          <Smile size={14} strokeWidth={1.8} />
          <span className="fc-gen-opt-label">{styleLabel}</span>
          {!globalSettingsReadOnly ? <ChevronDown size={12} strokeWidth={2} /> : null}
        </button>
        <button
          type="button"
          className={`fc-gen-opt-btn${open === 'subtitle' || !subtitleUsesModel ? ' active' : ''}${globalSettingsReadOnly ? ' is-readonly' : ''}`}
          disabled={globalLocked}
          onClick={() => {
            if (globalSettingsReadOnly) return
            setOpen((c) => (c === 'subtitle' ? null : 'subtitle'))
          }}
          title={globalSettingsReadOnly ? translateUi("workflow.projectSettingReadOnlyHere", { v1: String(subtitleLabel) }) : translateUi("workflow.subtitleSettings")}
        >
          <Type size={14} strokeWidth={1.8} />
          <span className="fc-gen-opt-label">{subtitleLabel}</span>
          {!globalSettingsReadOnly ? <ChevronDown size={12} strokeWidth={2} /> : null}
        </button>
        <button
          type="button"
          className={`fc-gen-opt-btn${open === 'intro' || !introEnabled ? ' active' : ''}${globalSettingsReadOnly ? ' is-readonly' : ''}`}
          disabled={globalLocked}
          onClick={() => {
            if (globalSettingsReadOnly) return
            setOpen((c) => (c === 'intro' ? null : 'intro'))
          }}
          title={globalSettingsReadOnly ? translateUi("workflow.projectSettingReadOnlyHere", { v1: String(introLabel) }) : translateUi("workflow.characterIntroductionOverlays")}
        >
          <Users size={14} strokeWidth={1.8} />
          <span className="fc-gen-opt-label">{introLabel}</span>
          {!globalSettingsReadOnly ? <ChevronDown size={12} strokeWidth={2} /> : null}
        </button>
        <button
          type="button"
          className={`fc-gen-opt-btn${open === 'model' ? ' active' : ''}`}
          disabled={disabled}
          onClick={() => setOpen((c) => (c === 'model' ? null : 'model'))}
        >
          <BarChart3 size={14} strokeWidth={1.8} />
          <span className="fc-gen-opt-label">{modelLabel}</span>
          <ChevronDown size={12} strokeWidth={2} />
        </button>
        <button
          type="button"
          className={`fc-gen-opt-btn${open === 'link' || linkLastFrame ? ' active' : ''}${globalSettingsReadOnly ? ' is-readonly' : ''}`}
          disabled={globalLocked}
          onClick={() => {
            if (globalSettingsReadOnly) return
            setOpen((c) => (c === 'link' ? null : 'link'))
          }}
          title={globalSettingsReadOnly ? translateUi("workflow.projectSettingReadOnlyHere", { v1: String(linkLastFrame ? translateUi("workflow.lastFrameContinuity") : translateUi("workflow.concurrentGeneration")) }) : translateUi("workflow.lastFrameContinuityBetweenShots")}
        >
          <Link2 size={14} strokeWidth={1.8} />
          <span className="fc-gen-opt-label">{linkLastFrame ? translateUi("workflow.lastFrameContinuity") : translateUi("workflow.concurrentGeneration")}</span>
          {!globalSettingsReadOnly ? <ChevronDown size={12} strokeWidth={2} /> : null}
        </button>
        <button
          type="button"
          className="fc-gen-opt-btn drama-seedance-help-btn"
          disabled={disabled}
          title={translateUi("workflow.seedanceInputsAndUsage")}
          aria-label={translateUi("workflow.seedanceInputsAndUsage")}
          onClick={() => setRulesOpen(true)}
        >
          <CircleHelp size={14} strokeWidth={1.8} />
        </button>
      </div>
      <SeedanceRulesModal open={rulesOpen} onClose={() => setRulesOpen(false)} />
      {open && panelStyle
        ? createPortal(
            <>
              {open === 'style' ? (
                <div
                  className="fc-gen-opt-panel fc-gen-style-panel drama-ep-opt-panel fc-gen-opt-panel--portal"
                  style={panelStyle}
                  role="dialog"
                  aria-label={translateUi("workflow.videoStyle")}
                >
                  <div className="fc-gen-opt-panel-title">{translateUi("workflow.videoStyle")}</div>
                  <div className="fc-gen-style-grid">
                    {IMAGE_STYLE_OPTIONS.map((opt) => {
                      const selected = styleId === opt.id
                      return (
                        <button
                          key={opt.id}
                          type="button"
                          className={`fc-gen-style-card${selected ? ' selected' : ''}`}
                          onMouseDown={(e) => e.preventDefault()}
                          onClick={() => {
                            onStyleChange(opt.id)
                            setOpen(null)
                          }}
                        >
                          <DramaImageStylePreviewImg styleId={opt.id} alt={opt.label} />
                          <span>{opt.label}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              ) : null}
              {open === 'model' ? (
                <div
                  className="fc-gen-opt-panel drama-ep-opt-panel fc-gen-opt-panel--portal"
                  style={panelStyle}
                  role="dialog"
                  aria-label={translateUi("workflow.videoModel")}
                >
                  <div className="fc-gen-opt-panel-title">{translateUi("workflow.videoModel")}</div>
                  <DramaMediaModelPicker
                    models={videoModels}
                    selectedId={modelId}
                    emptyHint={translateUi("workflow.saveADefaultModelUnderModelsInTheAdmin")}
                    onSelect={(opt) => {
                      onModelChange(opt.id)
                      setOpen(null)
                    }}
                  />
                </div>
              ) : null}
              {open === 'link' ? (
                <div
                  className="fc-gen-opt-panel drama-ep-opt-panel fc-gen-opt-panel--portal"
                  style={panelStyle}
                  role="dialog"
                  aria-label={translateUi("workflow.shotContinuity")}
                >
                  <div className="fc-gen-opt-panel-title">{translateUi("workflow.shotContinuity")}</div>
                  <label className="drama-ep-link-last-frame">
                    <input
                      type="checkbox"
                      checked={linkLastFrame}
                      disabled={disabled}
                      onChange={(e) => onLinkLastFrameChange(e.target.checked)}
                    />
                    <span>
                      {translateUi("workflow.continueFromThePreviousShotSLastFrame")}
                      <em>
                        {translateUi("workflow.enabledByDefaultShotsRunInOrderSubmitLater")}
                      </em>
                    </span>
                  </label>
                </div>
              ) : null}
              {open === 'subtitle' ? (
                <div
                  className="fc-gen-opt-panel drama-ep-opt-panel fc-gen-opt-panel--portal"
                  style={panelStyle}
                  role="dialog"
                  aria-label={translateUi("workflow.subtitleSettings")}
                >
                  <div className="fc-gen-opt-panel-title">{translateUi("workflow.subtitleSettings")}</div>
                  <label className="drama-ep-link-last-frame">
                    <input
                      type="radio"
                      name="episode-subtitle-mode"
                      checked={subtitleMode === 'model'}
                      disabled={disabled}
                      onChange={() => onSubtitleModeChange('model')}
                    />
                    <span>
                      {translateUi("workflow.modelGeneratedSubtitles_dfe6c0")}
                      <em>
                        {translateUi("workflow.addsSubtitleCuesToTheCurrentScriptsSoThe")}
                      </em>
                    </span>
                  </label>
                  <label className="drama-ep-link-last-frame">
                    <input
                      type="radio"
                      name="episode-subtitle-mode"
                      checked={subtitleMode === 'post'}
                      disabled={disabled}
                      onChange={() => onSubtitleModeChange('post')}
                    />
                    <span>
                      {translateUi("workflow.postProductionSubtitles_4b3cc7")}
                      <em>
                        {translateUi("workflow.removesSubtitlePromptsFromTheScriptsPreviewOrExport")}
                      </em>
                    </span>
                  </label>
                </div>
              ) : null}
              {open === 'intro' ? (
                <div
                  className="fc-gen-opt-panel drama-ep-opt-panel fc-gen-opt-panel--portal"
                  style={panelStyle}
                  role="dialog"
                  aria-label={translateUi("workflow.characterIntroductions")}
                >
                  <div className="fc-gen-opt-panel-title">{translateUi("workflow.characterIntroductionOverlays")}</div>
                  <label className="drama-ep-link-last-frame">
                    <input
                      type="radio"
                      name="episode-character-intro-mode"
                      checked={characterIntroMode === 'model'}
                      disabled={disabled}
                      onChange={() => onCharacterIntroModeChange('model')}
                    />
                    <span>
                      {translateUi("workflow.modelGeneratedCharacterIntroductions")}
                      <em>
                        {translateUi("workflow.whenEnabledReplanningAddsIntroductionCuesForImportantCharacters")}
                      </em>
                    </span>
                  </label>
                  <label className="drama-ep-link-last-frame">
                    <input
                      type="radio"
                      name="episode-character-intro-mode"
                      checked={characterIntroMode === 'off'}
                      disabled={disabled}
                      onChange={() => onCharacterIntroModeChange('off')}
                    />
                    <span>
                      {translateUi("workflow.disableCharacterIntroductions")}
                      <em>
                        {translateUi("workflow.removesIntroductionOverlayLinesAndPreventsIntroductionCardsIn")}
                      </em>
                    </span>
                  </label>
                </div>
              ) : null}
            </>,
            document.body,
          )
        : null}
    </div>
  )
}
