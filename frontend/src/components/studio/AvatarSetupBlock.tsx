import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { api } from '../../api'
import type { AvatarEngine, AvatarServiceStatus, Project } from '../../api'

/** 引擎选项：空串跟随后台默认 */
const ENGINES: { id: AvatarEngine; label: string; desc: string }[] = [
  { id: '', label: '后台默认', desc: '跟随管理后台「数字人」设置' },
  { id: 'infinitetalk', label: 'InfiniteTalk', desc: '口型与表情更自然（推荐）' },
  { id: 'longcat', label: 'LongCat', desc: '美团 LongCat 1.5，动作幅度更大' },
]

type Props = {
  project: Project
  engine: AvatarEngine
  onEngineChange: (engine: AvatarEngine) => void
  onProjectUpdated: (project: Project) => void
  disabled?: boolean
}

/**
 * 数字人口播设置：上传半身照、选择引擎、提示服务状态。
 * 照片立即上传保存；引擎随「保存并继续」一起提交。
 */
export default function AvatarSetupBlock({ project, engine, onEngineChange, onProjectUpdated, disabled }: Props) {
  /* 上传与服务状态 */
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [service, setService] = useState<AvatarServiceStatus | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    api
      .avatarStatus()
      .then(setService)
      .catch(() => setService(null))
  }, [])

  /** 选择照片后立即上传 */
  async function onFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setUploading(true)
    setError('')
    try {
      onProjectUpdated(await api.uploadAvatarImage(project.id, file))
    } catch (err) {
      setError(err instanceof Error ? err.message : '照片上传失败')
    } finally {
      setUploading(false)
    }
  }

  const portrait = project.avatar_image_url ? api.assetUrl(project.avatar_image_url, project.updated_at) : ''
  const hasVideos = (project.shots || []).some((s) => s.video_url)

  return (
    <div className="pf-style-block">
      <h3>数字人</h3>
      {service && !service.configured ? (
        <p className="pf-avatar-warn">数字人服务尚未配置，请联系管理员在后台「系统设置 → 数字人」填写服务地址。</p>
      ) : service && !service.online ? (
        <p className="pf-avatar-warn">数字人服务暂时连不上，可以先配音，生成视频前请确认 GPU 服务已启动。</p>
      ) : null}
      <div className="pf-avatar-setup">
        <button
          type="button"
          className="pf-avatar-photo"
          disabled={disabled || uploading}
          onClick={() => fileRef.current?.click()}
        >
          {portrait ? <img src={portrait} alt="数字人照片" /> : <span>{uploading ? '上传中…' : '上传半身照'}</span>}
          {portrait ? <span className="pf-avatar-photo-change">{uploading ? '上传中…' : '更换'}</span> : null}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          hidden
          onChange={onFile}
        />
        <div className="pf-avatar-tips">
          <p>正面半身照，脸部清晰、光线均匀、嘴巴闭合，背景简洁。</p>
          <p>建议竖版 9:16 照片，成片不需要裁切或补边。</p>
          <p>只上传本人或已获授权的人像，成片会标注 AI 生成。</p>
          {hasVideos ? <p className="pf-avatar-warn-inline">更换照片后，已生成的数字人视频需要重新生成。</p> : null}
        </div>
      </div>
      {error ? <p className="pf-avatar-warn">{error}</p> : null}
      <div className="pf-model-grid" style={{ marginTop: '0.75rem' }}>
        {ENGINES.map((m) => (
          <button
            key={m.id || 'default'}
            type="button"
            className={engine === m.id ? 'pf-model-opt selected' : 'pf-model-opt'}
            disabled={disabled}
            onClick={() => onEngineChange(m.id)}
          >
            <div className="pf-model-opt-title">
              <span>{m.label}</span>
            </div>
            <div className="pf-model-opt-desc">{m.desc}</div>
          </button>
        ))}
      </div>
    </div>
  )
}
