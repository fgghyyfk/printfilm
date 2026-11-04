import { useI18n } from '../../i18n'
import { translate as translateUi } from '../../i18n/runtime'
import { useEffect, useRef, useState, type ChangeEvent } from 'react'
import { api } from '../../api'
import type { AvatarEngine, AvatarOptions, AvatarServiceStatus, Project } from '../../api'
import AvatarProductionOptions from './AvatarProductionOptions'

/** 引擎选项：空串跟随后台默认 */
const ENGINES: { id: AvatarEngine; label: string; desc: string }[] = [
  { id: '', get label() { return translateUi("workflow.siteDefault") }, get desc() { return translateUi("workflow.useTheAvatarSettingsFromTheAdminPanel") } },
  { id: 'infinitetalk', label: 'InfiniteTalk', get desc() { return translateUi("workflow.moreNaturalLipSyncAndExpressionsRecommended") } },
  { id: 'longcat', label: 'LongCat', get desc() { return translateUi("workflow.meituanLongcat15WithBroaderMovements") } },
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
  const { t: translateUi } = useI18n()
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
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotUploadThePhoto"))
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
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotSave"))
    }
  }

  /** 生成 / 重新生成定妆照 */
  async function onGenerateLook() {
    setGenerating(true)
    setError('')
    try {
      onProjectUpdated(await api.generateAvatarLook(project.id, style, project.avatar_image_model || ''))
    } catch (err) {
      setError(err instanceof Error ? err.message : translateUi("workflow.couldNotGenerateTheStyledPortrait"))
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
      <h3>{translateUi("workflow.talkingHead")}</h3>
      {service && !service.configured ? (
        <p className="pf-avatar-warn">{translateUi("workflow.talkingHeadServiceIsNotConfiguredAskAnAdministrator")}</p>
      ) : service && !service.online ? (
        <p className="pf-avatar-warn">{translateUi("workflow.theTalkingHeadServiceIsTemporarilyUnavailableYouCan")}</p>
      ) : null}

      <div className="pf-avatar-setup">
        <div className="pf-avatar-col">
          <span className="pf-avatar-label">{translateUi("workflow.facePhoto")}</span>
          <button type="button" className="pf-avatar-photo" disabled={busy} onClick={() => fileRef.current?.click()}>
            {faceSrc ? <img src={faceSrc} alt={translateUi("workflow.facePhoto_26ccca")} /> : <span>{uploading ? translateUi("workflow.uploading") : translateUi("workflow.uploadPhoto")}</span>}
            {faceSrc ? <span className="pf-avatar-photo-change">{uploading ? translateUi("workflow.uploading") : translateUi("workflow.replace")}</span> : null}
          </button>
          <input ref={fileRef} type="file" accept="image/jpeg,image/png,image/webp" hidden onChange={onFile} />
        </div>

        {aiReady ? (
          <div className="pf-avatar-col">
            <span className="pf-avatar-label">{translateUi("workflow.styledPortrait")}</span>
            <div className="pf-avatar-photo pf-avatar-look">
              {generating ? (
                <span>{translateUi("workflow.generating")}</span>
              ) : lookSrc ? (
                <img src={lookSrc} alt={translateUi("workflow.styledPortrait_beb785")} />
              ) : (
                <span>{translateUi("workflow.notGenerated")}</span>
              )}
            </div>
          </div>
        ) : null}

        <div className="pf-avatar-tips">
          {aiReady ? (
            <>
              <p>{translateUi("workflow.onlyYourFaceIsTakenFromThePhotoClothing")}</p>
              <p>{translateUi("workflow.useAClearEvenlyLitFacePhotoItDoes")}</p>
            </>
          ) : (
            <>
              <p>{translateUi("workflow.portraitGenerationIsNotConfiguredSoLipSyncWill")}</p>
              <p>{translateUi("workflow.aVertical916PhotoIsRecommended")}</p>
            </>
          )}
          <p>{translateUi("workflow.uploadOnlyYourOwnPortraitOrOneYouAre")}</p>
          {hasVideos ? <p className="pf-avatar-warn-inline">{translateUi("workflow.afterChangingThePhotoOrStyledPortraitRegenerateAny")}</p> : null}
        </div>
      </div>

      {aiReady && faceSrc ? (
        <>
          <div className="pf-avatar-row">
            <span className="pf-avatar-label">{translateUi("ecomSet.form.style")}</span>
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
            <span className="pf-avatar-label">{translateUi("workflow.imageEditingModel")}</span>
            <div className="pf-avatar-chips">
              <button
                type="button"
                className={!currentModel ? 'pf-avatar-chip selected' : 'pf-avatar-chip'}
                disabled={busy}
                title={translateUi("workflow.siteDefault_c5581b", { v1: String(options?.default_image_model || '') })}
                onClick={() => void onModel('')}
              >
                {translateUi("workflow.siteDefault")}
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
            {generating ? translateUi("workflow.generatingAbout1060Seconds") : hasLook ? translateUi("workflow.regenerateStyledPortrait") : translateUi("workflow.generateStyledPortrait")}
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
