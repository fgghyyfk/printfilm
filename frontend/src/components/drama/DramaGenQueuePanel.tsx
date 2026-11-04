import { queueDisplayText } from '../../lib/dramaQueueDisplay'
import { useI18n } from '../../i18n'
import { translate as translateUi } from '../../i18n/runtime'
/** 漫剧全局生成队列：右下角圆钮，展示图片 / 视频等任务 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Clapperboard, ImageIcon, Layers, Octagon, Trash2, X } from 'lucide-react'
import { dramaApi } from '../../api/drama'
import { DramaGenTaskDetail } from './DramaGenTaskDetail'
import { formatDramaGenError } from '../../lib/dramaGenError'
import BillingTopupLink from '../billing/BillingTopupLink'
import { useFabDragPosition } from '../../hooks/useFabDragPosition'
import {
  clearFinishedDramaGenJobs,
  ensureEpisodeVideoStatusPoll,
  markVideoJobsCancelled,
  subscribeDramaGenQueueOpen,
  useDramaGenQueue,
  type DramaGenJob,
} from '../../lib/dramaGenQueue'
import '../../pages/drama/drama.css'

const OPEN_STORAGE_KEY = 'drama-gen-queue-fab-open'
const POS_STORAGE_KEY = 'drama-gen-queue-fab-pos'

const STATUS_LABEL: Record<string, string> = {
  get queued() { return translateUi("workflow.pending") },
  get running() { return translateUi("ecomSet.result.running") },
  get done() { return translateUi("ecomSet.status.done") },
  get failed() { return translateUi("ecomSet.status.failed") },
}

const IMAGE_SUBTYPE_LABEL: Record<string, string> = {
  get character() { return translateUi("workflow.characterImage") },
  get scene() { return translateUi("workflow.sceneImage") },
  get prop() { return translateUi("workflow.propImage") },
  get material() { return translateUi("workflow.referenceImage") },
}

// 读取折叠偏好
function readOpenPreference(): boolean {
  try {
    return localStorage.getItem(OPEN_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

// 任务类型展示
function jobTypeLabel(job: DramaGenJob): string {
  if (job.kind === 'video') return queueDisplayText(job.subtype) || translateUi("workflow.storyboardVideo")
  return IMAGE_SUBTYPE_LABEL[job.subtype] || job.subtype || translateUi("workflow.image")
}

// 任务图标
function JobKindIcon({ kind }: { kind: DramaGenJob['kind'] }) {
  if (kind === 'video') return <Clapperboard size={16} strokeWidth={1.75} aria-hidden />
  return <ImageIcon size={16} strokeWidth={1.75} aria-hidden />
}

// 渲染右下角统一生成队列
export function DramaGenQueuePanel() {
  const { t: translateUi } = useI18n()
  const queue = useDramaGenQueue()
  const [open, setOpen] = useState(readOpenPreference)
  const [detailJob, setDetailJob] = useState<DramaGenJob | null>(null)
  const { style: fabStyle, bindDrag, consumeDragSuppressClick } = useFabDragPosition(
    POS_STORAGE_KEY,
    { right: 20, bottom: 96 },
    56,
  )

  const active = useMemo(
    () => queue.filter((j) => j.status === 'queued' || j.status === 'running'),
    [queue],
  )
  const finished = useMemo(
    () => queue.filter((j) => j.status === 'done' || j.status === 'failed'),
    [queue],
  )
  const failed = useMemo(() => queue.filter((j) => j.status === 'failed'), [queue])

  // 列表按入队时间倒序；本地列表不代表服务端资源调度顺序。
  const sortedQueue = useMemo(
    () => [...queue].sort((a, b) => b.createdAt - a.createdAt),
    [queue],
  )
  useEffect(() => {
    try {
      localStorage.setItem(OPEN_STORAGE_KEY, open ? '1' : '0')
    } catch {
      /* ignore */
    }
  }, [open])

  // 入队后自动展开面板
  useEffect(() => {
    return subscribeDramaGenQueueOpen(() => {
      setOpen(true)
    })
  }, [])

  // 有进行中的分镜视频时后台轮询，离开编辑页也不中断
  useEffect(() => {
    if (active.some((job) => job.kind === 'video' && job.subtype === '分镜视频')) {
      ensureEpisodeVideoStatusPoll()
    }
  }, [active])

  // 详情随队列刷新同步同一 job
  useEffect(() => {
    if (!detailJob) return
    const latest = queue.find((j) => j.id === detailJob.id)
    if (!latest) {
      setDetailJob(null)
      return
    }
    if (latest !== detailJob) setDetailJob(latest)
  }, [queue, detailJob])

  const toggleOpen = useCallback(() => {
    if (consumeDragSuppressClick()) return
    setOpen((prev) => !prev)
  }, [consumeDragSuppressClick])

  const close = useCallback(() => {
    setOpen(false)
    setDetailJob(null)
  }, [])

  const cancelAllVideo = useCallback(async () => {
    try {
      await dramaApi.cancelAllVideoJobs()
      markVideoJobsCancelled()
    } catch {
      /* ignore */
    }
  }, [])

  if (queue.length === 0) return null

  const badgeCount = active.length

  return (
    <div className="drama-gen-fab-root is-draggable" style={fabStyle}>
      {open ? (
        <div className="drama-gen-fab-panel" role="dialog" aria-label={translateUi("workflow.generationQueue")}>
          {detailJob ? (
            <DramaGenTaskDetail job={detailJob} onClose={() => setDetailJob(null)} />
          ) : (
            <>
              <header className="drama-gen-fab-head">
                <div className="drama-gen-fab-title">
                  <Layers size={18} strokeWidth={1.75} aria-hidden />
                  <div>
                    <strong>{translateUi("workflow.generationQueue")}</strong>
                    <span>
                      {active.length > 0
                        ? translateUi("workflow.inProgress_2ed840", { v1: String(active.length) })
                        : failed.length > 0
                          ? translateUi("workflow.failed", { v1: String(failed.length) })
                          : finished.length > 0
                            ? translateUi("workflow.allComplete")
                            : ''}
                    </span>
                  </div>
                </div>
                <div className="drama-gen-fab-actions">
                  {active.some((j) => j.kind === 'video') ? (
                    <button
                      type="button"
                      className="drama-gen-fab-icon-btn"
                      onClick={cancelAllVideo}
                      title={translateUi("workflow.cancelAllVideoTasks")}
                      aria-label={translateUi("workflow.cancelAllVideoTasks")}
                    >
                      <Octagon size={16} />
                    </button>
                  ) : null}
                  {finished.length > 0 ? (
                    <button
                      type="button"
                      className="drama-gen-fab-icon-btn"
                      onClick={clearFinishedDramaGenJobs}
                      title={translateUi("workflow.clearFinishedTasks")}
                      aria-label={translateUi("workflow.clearFinishedTasks")}
                    >
                      <Trash2 size={16} />
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="drama-gen-fab-icon-btn"
                    onClick={close}
                    title={translateUi("workflow.closeQueue")}
                    aria-label={translateUi("workflow.closeQueue")}
                  >
                    <X size={18} />
                  </button>
                </div>
              </header>

              <ul className="drama-gen-fab-list">
                {sortedQueue.map((job) => {
                  const errView = job.status === 'failed' ? formatDramaGenError(job.error) : null
                  return (
                    <li key={job.id}>
                      <button
                        type="button"
                        className={`drama-gen-fab-item is-${job.status} is-clickable`}
                        onClick={() => setDetailJob(job)}
                      >
                        <div className="drama-gen-fab-item-head">
                          <div className="drama-gen-fab-item-main">
                            <span className="drama-gen-fab-kind">
                              <JobKindIcon kind={job.kind} />
                              <em>{job.kind === 'video' ? translateUi("workflow.video") : translateUi("workflow.image")}</em>
                            </span>
                            <span className="drama-gen-fab-name">{job.title}</span>
                            <span className="drama-gen-fab-type">{jobTypeLabel(job)}</span>
                            {job.message ? (
                              <span className="drama-gen-fab-msg">{queueDisplayText(job.message)}</span>
                            ) : null}
                          </div>
                          <span className="drama-gen-fab-status">
                            {STATUS_LABEL[job.status]}
                          </span>
                        </div>
                        {errView ? (
                          <div className="drama-gen-fab-error-block">
                            <p className="drama-gen-fab-error-title">{errView.title}</p>
                            <p className="drama-gen-fab-error">{errView.message}</p>
                            {errView.suggestion ? (
                              <p className="drama-gen-fab-error-tip">{errView.suggestion}</p>
                            ) : null}
                            {errView.billingBlocked ? (
                              <p className="drama-gen-fab-error-tip">
                                <BillingTopupLink className="pf-link pf-billing-topup-link drama-gen-fab-topup-link" />
                              </p>
                            ) : null}
                            {errView.upstreamAccountBlocked ? (
                              <p className="drama-gen-fab-error-tip drama-gen-fab-upstream-tip">
                                {translateUi("workflow.anAdministratorMustTopUpTheTokenfreeSeedreamAccount")}
                              </p>
                            ) : null}
                            <span className="drama-gen-fab-open-hint">{translateUi("workflow.viewReason")}</span>
                          </div>
                        ) : (
                          <span className="drama-gen-fab-open-hint">{translateUi("workflow.viewDetails")}</span>
                        )}
                        {(job.status === 'queued' || job.status === 'running') && (
                          <div className="drama-gen-fab-bar" aria-hidden />
                        )}
                      </button>
                    </li>
                  )
                })}
              </ul>

              {active.length > 0 ? (
                <footer className="drama-gen-fab-foot">
                  <span className="drama-gen-fab-foot-dot" aria-hidden />
                </footer>
              ) : null}
            </>
          )}
        </div>
      ) : null}

      <button
        type="button"
        className={`drama-gen-fab-btn is-draggable${active.length > 0 ? ' is-busy' : ''}${failed.length > 0 && active.length === 0 ? ' is-failed' : ''}`}
        onClick={toggleOpen}
        title={open ? translateUi("workflow.collapseGenerationQueueDraggable") : translateUi("workflow.expandGenerationQueueDraggable")}
        aria-expanded={open}
        aria-label={translateUi("workflow.generationQueue")}
        {...bindDrag}
      >
        <Layers size={22} strokeWidth={1.75} aria-hidden />
        {badgeCount > 0 ? <span className="drama-gen-fab-badge">{badgeCount}</span> : null}
      </button>
    </div>
  )
}
