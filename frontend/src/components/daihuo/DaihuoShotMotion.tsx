import { useState } from 'react'
import type { Shot } from '../../api'
import { setShotMotion } from '../../api/ecomHub'
import { useI18n } from '../../i18n'
import { isStaticShot, MOTION_ACTION_KEYS, motionActionOf, type MotionActionKey } from '../../lib/daihuoMotion'
import './daihuo.css'

type Props = {
  projectId: number
  pipelineMode: string | null | undefined
  shot: Shot
  /** 行忙（流水线或本镜生成中）时禁用 */
  disabled: boolean
  /** 切换成功后由页面重新拉取项目（静图口播首次打开动态会整体转为混合展示） */
  onChanged: () => Promise<void> | void
  onError: (message: string) => void
}

/** 带货分镜单镜「动态 / 静图」切换与镜头动作选择。 */
export default function DaihuoShotMotion({ projectId, pipelineMode, shot, disabled, onChanged, onError }: Props) {
  const { t } = useI18n()
  /* saving 本镜请求进行中，防止重复点击 */
  const [saving, setSaving] = useState(false)
  const still = isStaticShot(pipelineMode, shot)
  const action = motionActionOf(shot.camera)

  // 提交切换或动作，成功后刷新项目；失败把后端中文提示交给页面错误条
  async function save(body: { motion: boolean; action?: MotionActionKey }) {
    if (saving) return
    setSaving(true)
    try {
      await setShotMotion(projectId, shot.id, body)
      await onChanged()
    } catch (err) {
      onError(err instanceof Error ? err.message : t('daihuo.motion.failed'))
    } finally {
      setSaving(false)
    }
  }

  const locked = disabled || saving
  return (
    <div className="pf-daihuo-motion" aria-label={t('daihuo.motion.title')}>
      <div className="pf-daihuo-motion-toggle" role="radiogroup" aria-label={t('daihuo.motion.title')}>
        <button type="button" role="radio" aria-checked={!still} className={!still ? 'active' : ''}
          disabled={locked || !still} onClick={() => void save({ motion: true })}>
          {t('daihuo.motion.dynamic')}
        </button>
        <button type="button" role="radio" aria-checked={still} className={still ? 'active' : ''}
          disabled={locked || still} onClick={() => void save({ motion: false })}>
          {t('daihuo.motion.static')}
        </button>
      </div>
      {still ? (
        <span className="pf-muted">{t('daihuo.motion.staticHint')}</span>
      ) : (
        <label className="pf-daihuo-motion-action">
          <span className="sr-only">{t('daihuo.motion.action')}</span>
          <select value={action || ''} disabled={locked} aria-label={t('daihuo.motion.action')}
            onChange={(event) => void save({ motion: true, action: event.target.value as MotionActionKey })}>
            {action ? null : <option value="" disabled>{t('daihuo.motion.action')}</option>}
            {MOTION_ACTION_KEYS.map((key) => (
              <option key={key} value={key}>{t(`daihuo.motion.actions.${key}`)}</option>
            ))}
          </select>
          {!shot.video_url ? <span className="pf-muted">{t('daihuo.motion.pending')}</span> : null}
        </label>
      )}
    </div>
  )
}
