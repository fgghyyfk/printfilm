import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { api } from '../../api'
import type { AvatarEngine, AvatarOptions, AvatarServiceStatus, Project } from '../../api'
import AvatarProductionOptions from './AvatarProductionOptions'

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
 * 数字人口播设置：① 上传人脸照（只取脸）② 选风格与改图模型生成定妆照 ③ 选对口型引擎。
 * 照片、定妆照、改图模型都立即保存；引擎随「保存并继续」一起提交。
 * 后台没配形象生成服务时，跳过 ②，直接用上传的照片对口型。
 */
export default function AvatarSetupBlock({ project, engine, onEngineChange, onProjectUpdated, disabled }: Props) {
  /* 上传 / 定妆照生成状态 */
  const [uploading, setUploading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState('')
  /* 服务状态与可选项 */
  const [service, setService] = useState<AvatarServiceStatus | null>(null)
  const [options, setOptions] = useState<AvatarOptions | null>(null)
  const [style, setStyle] = useState(project.avatar_look_style || 'cartoon3d')
  const fileRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    api.avatarStatus().then(setService).catch(() => setService(null))
    api.avatarOptions().then(setOptions).catch(() => setOptions(null))
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

  /** 切换改图模型：立即保存到项目（定妆照与场景图都用它） */
  async function onModel(model: string) {
    setError('')
    try {
      onProjectUpdated(await api.updateProject(project.id, { avatar_image_model: model }))
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    }
  }

  /** 生成 / 重新生成定妆照 */
  async function onGenerateLook() {
    setGenerating(true)
    setError('')
    try {
      onProjectUpdated(await api.generateAvatarLook(project.id, style, project.avatar_image_model || ''))
    } catch (err) {
      setError(err instanceof Error ? err.message : '定妆照生成失败')
    } finally {
      setGenerating(false)
    }
  }

  const face = project.avatar_face_url || project.avatar_image_url || ''
  const faceSrc = face ? api.assetUrl(face, project.updated_at) : ''
  const hasLook = Boolean(project.avatar_image_url && project.avatar_image_url !== face)
  const lookSrc = hasLook ? api.assetUrl(project.avatar_image_url || '', project.updated_at) : ''
  const hasVideos = (project.shots || []).some((s) => s.video_url)
  const aiReady = Boolean(options?.ai_configured)
  const currentModel = project.avatar_image_model || ''
  const busy = disabled || uploading || generating

  return (
    <div className="pf-style-block">
      <h3>数字人</h3>
      {service && !service.configured ? (
        <p className="pf-avatar-warn">数字人服务尚未配置，请联系管理员在后台「系统设置 → 数字人」填写服务地址。</p>
      ) : service && !service.online ? (
        <p className="pf-avatar-warn">数字人服务暂时连不上，可以先准备素材，生成视频前请确认 GPU 服务已启动。</p>
      ) : null}

      <div className="pf-avatar-setup">
        <div className="pf-avatar-col">
          <span className="pf-avatar-label">① 人脸照</span>
          <button type="button" className="pf-avatar-photo" disabled={busy} onClick={() => fileRef.current?.click()}>
            {faceSrc ? <img src={faceSrc} alt="人脸照" /> : <span>{uploading ? '上传中…' : '上传照片'}</span>}
            {faceSrc ? <span className="pf-avatar-photo-change">{uploading ? '上传中…' : '更换'}</span> : null}
          </button>
          <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={onFile} />
        </div>

        {aiReady ? (
          <div className="pf-avatar-col">
            <span className="pf-avatar-label">② 定妆照</span>
            <div className="pf-avatar-photo pf-avatar-look">
              {generating ? (
                <span>生成中…</span>
              ) : lookSrc ? (
                <img src={lookSrc} alt="定妆照" />
              ) : (
                <span>未生成</span>
              )}
            </div>
          </div>
        ) : null}

        <div className="pf-avatar-tips">
          {aiReady ? (
            <>
              <p>照片只取脸：服装、姿势、背景都会重新生成，每段口播还会换成与内容相关的场景。</p>
              <p>正脸清晰、光线均匀即可，不必是半身照。</p>
            </>
          ) : (
            <>
              <p>未配置形象生成服务，将直接用上传的照片对口型：请用正面半身照，脸部清晰、嘴巴闭合、背景简洁。</p>
              <p>建议竖版 9:16 照片。</p>
            </>
          )}
          <p>只上传本人或已获授权的人像，成片会标注 AI 生成。</p>
          {hasVideos ? <p className="pf-avatar-warn-inline">更换照片或定妆照后，已生成的数字人视频需要重新生成。</p> : null}
        </div>
      </div>

      {aiReady && faceSrc ? (
        <>
          <div className="pf-avatar-row">
            <span className="pf-avatar-label">风格</span>
            <div className="pf-avatar-chips">
              {options?.look_styles.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  className={style === s.id ? 'pf-avatar-chip selected' : 'pf-avatar-chip'}
                  disabled={busy}
                  onClick={() => setStyle(s.id)}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>
          <div className="pf-avatar-row">
            <span className="pf-avatar-label">改图模型</span>
            <div className="pf-avatar-chips">
              <button
                type="button"
                className={!currentModel ? 'pf-avatar-chip selected' : 'pf-avatar-chip'}
                disabled={busy}
                title={`后台默认：${options?.default_image_model || ''}`}
                onClick={() => void onModel('')}
              >
                后台默认
              </button>
              {options?.image_models.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  className={currentModel === m.id ? 'pf-avatar-chip selected' : 'pf-avatar-chip'}
                  disabled={busy}
                  title={m.hint}
                  onClick={() => void onModel(m.id)}
                >
                  {m.label}
                </button>
              ))}
            </div>
          </div>
          <button
            type="button"
            className="pf-btn pf-btn-outline pf-btn-sm pf-avatar-gen"
            disabled={busy}
            onClick={() => void onGenerateLook()}
          >
            {generating ? '生成中，约 10–60 秒…' : hasLook ? '重新生成定妆照' : '生成定妆照'}
          </button>
        </>
      ) : null}

      {error ? <p className="pf-avatar-warn">{error}</p> : null}
      {aiReady ? (
        <AvatarProductionOptions project={project} onProjectUpdated={onProjectUpdated} disabled={busy} />
      ) : null}
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
