import { useState } from 'react'
import { api } from '../../api'
import type { Project } from '../../api'

/** 单个选项组：标签 + 一排胶囊按钮 */
type Choice<T extends string> = { id: T; label: string; title?: string }

const SCENE_MODES: Choice<'per_segment' | 'fixed'>[] = [
  { id: 'per_segment', label: '每段换场景', title: '每段口播换一个与内容相关的背景（适合科普讲解）' },
  { id: 'fixed', label: '全片固定场景', title: '整条视频同一个场景（适合观点口播、个人 IP）' },
]
const FRAMINGS: Choice<'waist' | 'chest'>[] = [
  { id: 'waist', label: '腰部以上' },
  { id: 'chest', label: '胸像近景', title: '口播博主式近景，脸更大' },
]
const SCRIPT_STYLES: Choice<'kepu' | 'opinion'>[] = [
  { id: 'kepu', label: '科普讲解' },
  { id: 'opinion', label: '观点口播', title: '个人 IP 观点分享，标题拆成两行钩子' },
]
const RESOLUTIONS: Choice<'' | '480p' | '720p'>[] = [
  { id: '', label: '后台默认' },
  { id: '480p', label: '480p' },
  { id: '720p', label: '720p', title: '更清晰，GPU 耗时约翻倍' },
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
      setError(err instanceof Error ? err.message : '保存失败')
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
      {row('文案', SCRIPT_STYLES, project.avatar_script_style || 'kepu', (v) => void save({ avatar_script_style: v }))}
      {row('场景', SCENE_MODES, project.avatar_scene_mode || 'per_segment', (v) => void save({ avatar_scene_mode: v }))}
      {row('构图', FRAMINGS, project.avatar_framing || 'waist', (v) => void save({ avatar_framing: v }))}
      {row('清晰度', RESOLUTIONS, project.avatar_resolution || '', (v) => void save({ avatar_resolution: v }))}
      {opinion ? (
        <div className="pf-avatar-row">
          <span className="pf-avatar-label">标题第二行</span>
          <input
            className="pf-avatar-input"
            value={title2}
            maxLength={32}
            placeholder="留空则写稿时自动生成，如「怕选错平台」"
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
