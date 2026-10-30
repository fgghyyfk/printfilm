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

  /** 确认后提交批量任务 */
  async function submit() {
    if (!topics.length) return
    if (!window.confirm(`将新建 ${topics.length} 个数字人项目并全自动出片（GPU 排队，单条约 1–3 小时），确定吗？`)) return
    setSubmitting(true)
    setError('')
    try {
      await api.createAvatarBatch(project.id, topics, sourceType)
      navigate('/history')
    } catch (err) {
      setError(err instanceof Error ? err.message : '批量提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="pf-style-block">
      <h3>批量生产</h3>
      <p className="pf-muted" style={{ fontSize: '0.78rem', margin: '0 0 0.6rem' }}>
        沿用上面的形象、音色、场景、构图和文案风格，每个选题生成一条完整视频，最多 50 个。
      </p>
      <div className="pf-avatar-chips" style={{ marginBottom: '0.5rem' }}>
        <button
          type="button"
          className={sourceType === 'theme' ? 'pf-avatar-chip selected' : 'pf-avatar-chip'}
          onClick={() => setSourceType('theme')}
        >
          按选题（每行一个）
        </button>
        <button
          type="button"
          className={sourceType === 'script' ? 'pf-avatar-chip selected' : 'pf-avatar-chip'}
          onClick={() => setSourceType('script')}
        >
          按口播稿（空行分隔）
        </button>
      </div>
      <textarea
        className="pf-avatar-batch-input"
        rows={6}
        value={text}
        disabled={disabled || submitting || !ready}
        placeholder={
          sourceType === 'theme'
            ? '长寿行业不怕没机会，怕选错平台\n高端健康管理，为什么要去香港\n…'
            : '第一篇口播稿全文…\n\n第二篇口播稿全文…'
        }
        onChange={(e) => setText(e.target.value)}
      />
      {!ready ? <p className="pf-avatar-warn">请先上传数字人照片（并生成定妆照）再批量生产。</p> : null}
      {error ? <p className="pf-avatar-warn">{error}</p> : null}
      <button
        type="button"
        className="pf-btn pf-btn-outline pf-btn-sm pf-avatar-gen"
        disabled={disabled || submitting || !ready || topics.length === 0}
        onClick={() => void submit()}
      >
        {submitting ? '提交中…' : `批量生成 ${topics.length || ''} 条`}
      </button>
    </div>
  )
}
