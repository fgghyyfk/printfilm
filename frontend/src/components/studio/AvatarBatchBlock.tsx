import { useI18n } from '../../i18n'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api'
import type { Project } from '../../api'

type Props = {
  project: Project
  disabled?: boolean
}

/**
 * 数字人批量生产：沿用当前项目的形象、音色、场景、构图与文案风格，每行一个选题（或一篇口播稿）各建一个项目并全自动出片。
 * GPU 串行排队，提交后到「作品」列表看进度。
 */
export default function AvatarBatchBlock({ project, disabled }: Props) {
  const { t: translateUi } = useI18n()
  const navigate = useNavigate()
  /* 输入与提交状态 */
  const [text, setText] = useState('')
  const [sourceType, setSourceType] = useState<'theme' | 'script'>('theme')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  // 选题模式每行一个；口播稿模式用空行分隔多篇
  const topics =
    sourceType === 'theme'
      ? text.split('\n').map((t) => t.trim()).filter((t) => t.length >= 2)
      : text.split(/\n\s*\n/).map((t) => t.trim()).filter((t) => t.length >= 2)
  const ready = Boolean(project.avatar_image_url)

  /** 先估价，确认后提交批量任务（余额不足由后端拒绝并给出中文提示） */
  async function submit() {
    if (!topics.length) return
    setSubmitting(true)
    setError('')
    let costLine = ''
    try {
      const est = await api.estimateAvatarBatch(project.id, topics, sourceType)
      if (est.billing_enabled) {
        costLine = translateUi("workflow.batchEstimatedCost", { v1: String((est.estimate_fen / 100).toFixed(2)), v2: String((est.balance_fen / 100).toFixed(2)) })
      }
    } catch {
      // 估价失败不阻止提交，提交时后端仍会校验余额
    }
    const ok = window.confirm(
      translateUi("workflow.createTalkingHeadProjectsAndGenerateTheirVideosAutomatically", { v1: String(topics.length), v2: String(costLine) }),
    )
    if (!ok) {
      setSubmitting(false)
      return
    }
    try {
      await api.createAvatarBatch(project.id, topics, sourceType)
      navigate('/history')
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotSubmitTheBatch"))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="pf-style-block">
      <h3>{translateUi("workflow.batchProduction")}</h3>
      <p className="pf-muted" style={{ fontSize: '0.78rem', margin: '0 0 0.6rem' }}>
        {translateUi("workflow.useThePortraitVoiceSceneFramingAndScriptStyle")}
      </p>
      <div className="pf-avatar-chips" style={{ marginBottom: '0.5rem' }}>
        <button
          type="button"
          className={sourceType === 'theme' ? 'pf-avatar-chip selected' : 'pf-avatar-chip'}
          onClick={() => setSourceType('theme')}
        >
          {translateUi("workflow.byTopicOnePerLine")}
        </button>
        <button
          type="button"
          className={sourceType === 'script' ? 'pf-avatar-chip selected' : 'pf-avatar-chip'}
          onClick={() => setSourceType('script')}
        >
          {translateUi("workflow.byScriptSeparateWithABlankLine")}
        </button>
      </div>
      <textarea
        className="pf-avatar-batch-input"
        rows={6}
        value={text}
        disabled={disabled || submitting || !ready}
        placeholder={
          sourceType === 'theme'
            ? translateUi("workflow.oneTopicPerLineAnotherTopicToExplain")
            : translateUi("workflow.fullNarrationScriptOneFullNarrationScriptTwo")
        }
        onChange={(e) => setText(e.target.value)}
      />
      {!ready ? <p className="pf-avatar-warn">{translateUi("workflow.uploadAPortraitAndPrepareTheStyledImageBefore")}</p> : null}
      {error ? <p className="pf-avatar-warn">{error}</p> : null}
      <button
        type="button"
        className="pf-btn pf-btn-outline pf-btn-sm pf-avatar-gen"
        disabled={disabled || submitting || !ready || topics.length === 0}
        onClick={() => void submit()}
      >
        {submitting ? translateUi("workflow.submitting") : translateUi("workflow.generateVideos", { v1: String(topics.length || '') })}
      </button>
    </div>
  )
}
