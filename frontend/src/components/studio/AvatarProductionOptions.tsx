import { useI18n } from '../../i18n'
import { translate as translateUi } from '../../i18n/runtime'
import { useState } from 'react'
import { api } from '../../api'
import type { Project } from '../../api'

/** 单个选项组：标签 + 一排胶囊按钮 */
type Choice<T extends string> = { id: T; label: string; title?: string }

const SCENE_MODES: Choice<'per_segment' | 'fixed'>[] = [
  { id: 'per_segment', get label() { return translateUi("workflow.aSceneForEachSegment") }, get title() { return translateUi("workflow.useARelevantBackgroundForEachSegmentSuitedTo") } },
  { id: 'fixed', get label() { return translateUi("workflow.oneSceneThroughout") }, get title() { return translateUi("workflow.keepOneSceneThroughoutSuitedToCommentaryAndPersonal") } },
]
const FRAMINGS: Choice<'waist' | 'chest'>[] = [
  { id: 'waist', get label() { return translateUi("workflow.waistUp") } },
  { id: 'chest', get label() { return translateUi("workflow.chestUpCloseUp") }, get title() { return translateUi("workflow.presenterStyleCloseUpWithALargerFaceIn") } },
]
const SCRIPT_STYLES: Choice<'kepu' | 'opinion'>[] = [
  { id: 'kepu', get label() { return translateUi("workflow.explainer") } },
  { id: 'opinion', get label() { return translateUi("workflow.commentary") }, get title() { return translateUi("workflow.personalBrandCommentaryWithATwoLineTitleHook") } },
]
const RESOLUTIONS: Choice<'' | '480p' | '720p'>[] = [
  { id: '', get label() { return translateUi("workflow.siteDefault") } },
  { id: '480p', label: '480p' },
  { id: '720p', label: '720p', get title() { return translateUi("workflow.sharperOutputGpuProcessingTakesAboutTwiceAsLong") } },
]

type Props = {
  project: Project
  onProjectUpdated: (project: Project) => void
  disabled?: boolean
}

/**
 * 数字人出片设置：场景模式、构图、文案风格、标题第二行、对口型清晰度。
 * 每项改动立即保存到项目；批量生产会沿用这些设置。
 */
export default function AvatarProductionOptions({ project, onProjectUpdated, disabled }: Props) {
  const { t: translateUi } = useI18n()
  /* 保存状态与标题第二行草稿 */
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [title2, setTitle2] = useState(project.avatar_title2 || '')

  /** 保存一组字段并回写项目 */
  async function save(body: Parameters<typeof api.updateProject>[1]) {
    setSaving(true)
    setError('')
    try {
      onProjectUpdated(await api.updateProject(project.id, body))
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotSave"))
    } finally {
      setSaving(false)
    }
  }

  /** 渲染一行胶囊选项 */
  function row<T extends string>(label: string, choices: Choice<T>[], value: T, onPick: (v: T) => void) {
    return (
      <div className="pf-avatar-row">
        <span className="pf-avatar-label">{label}</span>
        <div className="pf-avatar-chips">
          {choices.map((c) => (
            <button
              key={c.id || 'default'}
              type="button"
              title={c.title}
              className={value === c.id ? 'pf-avatar-chip selected' : 'pf-avatar-chip'}
              disabled={disabled || saving}
              onClick={() => onPick(c.id)}
            >
              {c.label}
            </button>
          ))}
        </div>
      </div>
    )
  }

  const opinion = project.avatar_script_style === 'opinion'
  return (
    <div className="pf-avatar-options">
      {row(translateUi("workflow.script"), SCRIPT_STYLES, project.avatar_script_style || 'kepu', (v) => void save({ avatar_script_style: v }))}
      {row(translateUi("workflow.scene"), SCENE_MODES, project.avatar_scene_mode || 'per_segment', (v) => void save({ avatar_scene_mode: v }))}
      {row(translateUi("workflow.framing"), FRAMINGS, project.avatar_framing || 'waist', (v) => void save({ avatar_framing: v }))}
      {row(translateUi("workflow.resolution"), RESOLUTIONS, project.avatar_resolution || '', (v) => void save({ avatar_resolution: v }))}
      {opinion ? (
        <div className="pf-avatar-row">
          <span className="pf-avatar-label">{translateUi("compliance.fields.avatar_title2")}</span>
          <input
            className="pf-avatar-input"
            value={title2}
            maxLength={32}
            placeholder={translateUi("workflow.leaveBlankToGenerateThisWhenWritingTheScript")}
            disabled={disabled || saving}
            onChange={(e) => setTitle2(e.target.value)}
            onBlur={() => {
              if (title2.trim() !== (project.avatar_title2 || '')) void save({ avatar_title2: title2.trim() })
            }}
          />
        </div>
      ) : null}
      {error ? <p className="pf-avatar-warn">{error}</p> : null}
    </div>
  )
}
