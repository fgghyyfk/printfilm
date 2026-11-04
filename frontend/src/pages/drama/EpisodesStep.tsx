import { useI18n } from '../../i18n'
/** 分集视频步骤：进页规则切分；支持全集规则重切与单集 AI 分镜 */
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Clapperboard, Film, Layers, Sparkles, Wand2 } from 'lucide-react'
import { dramaApi, resolveDramaMediaUrl, type DramaEpisode } from '../../api/drama'
import { dialog } from '../../lib/dialog'
import { loadDramaEpisodes } from '../../lib/dramaStoryboardNav'
import { readEpisodeSubtitleMode, subtitleModeUsesModelOutput } from '../../lib/dramaSubtitleBoard'
import { FragmentPlanSkillModal } from '../../components/drama/FragmentPlanSkillModal'
import { readFragmentGenerationStatus } from './dramaEpisodeEditUtils'

type EpisodesStepProps = {
  projectId: number
  onError: (m: string) => void
}

type EpisodeSummary = {
  epNo: number
  fragmentCount: number
  videoDone: number
  videoRunning: number
  videoFailed: number
  totalSec: number
  previewUrl: string
  planStatus: string
}

// 读取分集 AI 分镜状态
function readPlanStatus(ep: DramaEpisode): string {
  const active = (ep.active_tasks || []).find((task) => task.task_type === 'fragment_plan')
  if (
    active &&
    !active.cancel_requested &&
    ['pending', 'leased', 'running', 'awaiting_poll', 'awaiting_review'].includes(active.status)
  ) {
    return 'generating'
  }
  const st = ep.params?.fragment_plan_status
  return typeof st === 'string' ? st : ''
}

// 汇总单集分镜与视频进度
function summarizeEpisode(ep: DramaEpisode): EpisodeSummary {
  const frags = ep.fragments || []
  const activeFragmentIds = new Set<number>()
  for (const task of ep.active_tasks || []) {
    if (
      task.task_type === 'fragment_video' &&
      typeof task.fragment_id === 'number' &&
      !task.cancel_requested &&
      ['pending', 'leased', 'running', 'awaiting_poll', 'awaiting_review'].includes(task.status)
    ) {
      activeFragmentIds.add(task.fragment_id)
    }
  }
  let videoDone = 0
  let videoRunning = 0
  let videoFailed = 0
  let totalSec = 0
  let previewUrl = ''
  for (const frag of frags) {
    totalSec += frag.duration_sec && frag.duration_sec > 0 ? frag.duration_sec : 8
    const st = readFragmentGenerationStatus(frag).status
    if (st === 'done') videoDone += 1
    else if (st === 'queued' || st === 'running' || st === 'generating' || activeFragmentIds.has(frag.id))
      videoRunning += 1
    else if (st === 'failed') videoFailed += 1
    if (!previewUrl) {
      const raw = (frag.cover || frag.video || '').trim()
      if (raw) previewUrl = resolveDramaMediaUrl(raw)
    }
  }
  const epNo = Number(ep.params?.episodeNumber) || 0
  return {
    epNo,
    fragmentCount: frags.length,
    videoDone,
    videoRunning,
    videoFailed,
    totalSec,
    previewUrl,
    planStatus: readPlanStatus(ep),
  }
}

// 分集标题竖排展示用（过长截断）
function verticalTitleLabel(name: string, max = 14): string {
  const clean = (name || '').replace(/\s+/g, '')
  if (clean.length <= max) return clean
  return `${clean.slice(0, max - 1)}…`
}

// 渲染分集视频步骤
export function EpisodesStep({ projectId, onError }: EpisodesStepProps) {
  const { t: translateUi } = useI18n()
  const navigate = useNavigate()
  const [episodes, setEpisodes] = useState<DramaEpisode[]>([])
  const [loading, setLoading] = useState(true)
  const [reseeding, setReseeding] = useState(false)
  const [planningId, setPlanningId] = useState<number | null>(null)
  // planTarget 待确认 AI 分镜的分集
  const [planTarget, setPlanTarget] = useState<DramaEpisode | null>(null)
  const seeded = useRef(false)

  useEffect(() => {
    seeded.current = false
  }, [projectId])

  // 进页只读已有分集；force 才按剧本重切
  async function loadEpisodes(force = false) {
    const rows = await loadDramaEpisodes(projectId, force)
    setEpisodes(rows)
    return rows
  }

  useEffect(() => {
    let cancelled = false
    let timer: number | undefined

    async function enter(initial: boolean) {
      if (initial) setLoading(true)
      try {
        let rows: DramaEpisode[]
        if (initial && !seeded.current) {
          seeded.current = true
          rows = await loadEpisodes(false)
        } else {
          rows = await dramaApi.listEpisodes(projectId)
          if (!cancelled) setEpisodes(rows)
        }
        if (cancelled) return
        const anyPlanning = rows.some((ep) => readPlanStatus(ep) === 'generating')
        if (anyPlanning) {
          timer = window.setTimeout(() => {
            void enter(false)
          }, 2500)
        }
      } catch (err) {
        if (initial) onError(err instanceof Error ? err.message : translateUi("workflow.couldNotLoadEpisodes"))
        try {
          const rows = await dramaApi.listEpisodes(projectId)
          if (!cancelled) setEpisodes(rows)
        } catch {
          /* ignore */
        }
      } finally {
        if (initial && !cancelled) setLoading(false)
      }
    }
    void enter(true)
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [projectId, onError])

  async function handleReseed() {
    if (reseeding || planningId != null) return
    const ok = await dialog.confirm({
      title: translateUi("workflow.rebuildAllStoryboardsByRules"),
      message:
        translateUi("workflow.rebuildEveryEpisodeSStoryboardsWithTheRuleEngine"),
      confirmText: translateUi("workflow.continueSplitting"),
      tone: 'danger',
    })
    if (!ok) return
    setReseeding(true)
    try {
      const rows = await loadEpisodes(true)
      await dialog.alert({
        title: translateUi("workflow.splittingComplete"),
        message: translateUi("workflow.updatedStoryboardsForEpisodesOpenAnEpisodeToReview", { v1: String(rows.length) }),
        tone: 'success',
      })
    } catch (err) {
      onError(err instanceof Error ? err.message : translateUi("workflow.couldNotSplitAgain"))
    } finally {
      setReseeding(false)
    }
  }

  // 打开 AI 分镜确认弹窗（勾选 Skill）
  function handlePlanEpisode(ep: DramaEpisode) {
    if (reseeding || planningId != null) return
    setPlanTarget(ep)
  }

  // 入队单集 LLM 分镜并轮询（字幕方式沿用该集当前设置）
  async function startPlanEpisode(ep: DramaEpisode, skillIds: number[]) {
    setPlanTarget(null)
    setPlanningId(ep.id)
    try {
      const subtitleMode = readEpisodeSubtitleMode(ep.params)
      await dramaApi.planEpisodeFragments(ep.id, {
        force: true,
        fallback_rules: true,
        skill_ids: skillIds,
        subtitle_enabled: subtitleModeUsesModelOutput(subtitleMode),
      })
      const started = Date.now()
      while (Date.now() - started < 10 * 60 * 1000) {
        await new Promise((r) => setTimeout(r, 2500))
        const cur = await dramaApi.getEpisode(ep.id)
        const st = readPlanStatus(cur)
        if (st === 'completed') {
          setEpisodes((prev) => prev.map((row) => (row.id === cur.id ? cur : row)))
          const count = Number(cur.params?.fragment_plan_count) || (cur.fragments || []).length
          const mode = String(cur.params?.fragment_plan_mode || 'llm')
          await dialog.alert({
            title: translateUi("workflow.storyboardComplete"),
            message:
              mode === 'rules_fallback'
                ? translateUi("workflow.usedRuleBasedSplittingProducingStoryboardClips", { v1: String(cur.name), v2: String(count) })
                : translateUi("workflow.aiStoryboardPlanningCompletedForProducingClips", { v1: String(cur.name), v2: String(count) }),
            tone: 'success',
          })
          return
        }
        if (st === 'failed') {
          throw new Error(String(cur.params?.fragment_plan_error || translateUi("workflow.aiStoryboardPlanningFailed")))
        }
      }
      throw new Error(translateUi("workflow.aiStoryboardPlanningTimedOutRefreshLater"))
    } catch (err) {
      onError(err instanceof Error ? err.message : translateUi("workflow.aiStoryboardPlanningFailed"))
    } finally {
      setPlanningId(null)
    }
  }

  const totalFragments = episodes.reduce((n, ep) => n + (ep.fragments?.length || 0), 0)
  const totalVideos = episodes.reduce(
    (n, ep) =>
      n + (ep.fragments || []).filter((f) => readFragmentGenerationStatus(f).status === 'done').length,
    0,
  )

  return (
    <div className="drama-episodes-step">
      <header className="drama-episodes-hero">
        <div className="drama-episodes-hero-main">
          <div className="drama-episodes-hero-icon" aria-hidden>
            <Film size={22} strokeWidth={1.75} />
          </div>
          <div>
            <h2>{translateUi("workflow.episodeVideos")}</h2>
            <p className="drama-episodes-hero-sub">
              {translateUi("workflow.total")} <strong>{episodes.length}</strong> {translateUi("workflow.episodes_c62f88", { v1: ' ' })}<strong>{totalFragments}</strong> {translateUi("workflow.storyboardShotsCompletedClips", { v1: ' ' })}<strong>{totalVideos}</strong>
            </p>
          </div>
        </div>
        <div className="drama-episodes-hero-actions">
          <button
            type="button"
            className="drama-btn-ghost drama-episodes-reseed-btn"
            disabled={reseeding || planningId != null || loading}
            onClick={() => void handleReseed()}
          >
            <Layers size={16} strokeWidth={1.75} aria-hidden />
            {reseeding ? translateUi("workflow.splitting") : translateUi("workflow.rebuildAllByRules")}
          </button>
        </div>
      </header>

      <div className="drama-episodes-tips" role="note">
        <Sparkles size={15} strokeWidth={1.75} aria-hidden />
        <span>{translateUi("workflow.forAnIndividualEpisodeChoose")} <strong>{translateUi("workflow.aiStoryboards")}</strong> {translateUi("workflow.forDetailedPlanningOpen")} <strong>{translateUi("workflow.edit")}</strong> {translateUi("workflow.toEditScriptsAndGenerateClips")}</span>
      </div>

      {loading ? (
        <div className="drama-episode-grid" aria-busy="true" aria-label={translateUi("workflow.loadEpisodes")}>
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="drama-ep-card drama-ep-card-skeleton" />
          ))}
        </div>
      ) : episodes.length === 0 ? (
        <div className="drama-episodes-empty">
          <Clapperboard size={40} strokeWidth={1.25} aria-hidden />
          <p>{translateUi("workflow.noEpisodesYetCompleteTheEpisodeScriptStageFirst")}</p>
        </div>
      ) : (
        <div className="drama-episode-grid">
          {episodes.map((ep) => {
            const summary = summarizeEpisode(ep)
            const planning = planningId === ep.id || summary.planStatus === 'generating'
            const progress =
              summary.fragmentCount > 0
                ? Math.round((summary.videoDone / summary.fragmentCount) * 100)
                : 0
            const epLabel =
              summary.epNo > 0 ? translateUi("workflow.episode", { v1: String(summary.epNo) }) : translateUi("workflow.episode_ebaa6d", { v1: String(ep.id) })
            const statusLabel = planning
              ? translateUi("workflow.planningAiStoryboards")
              : summary.planStatus === 'failed'
                ? translateUi("workflow.storyboardPlanningFailed")
                : summary.videoRunning > 0
                  ? translateUi("workflow.generating_97cfaa", { v1: String(summary.videoRunning) })
                  : summary.videoFailed > 0
                    ? translateUi("workflow.failed_0c105a", { v1: String(summary.videoFailed) })
                    : summary.videoDone > 0
                      ? translateUi("workflow.clipsComplete", { v1: String(summary.videoDone), v2: String(summary.fragmentCount) })
                      : summary.planStatus === 'completed'
                        ? translateUi("workflow.storyboardShots", { v1: String(summary.fragmentCount) })
                        : summary.fragmentCount > 0
                          ? translateUi("workflow.storyboardShots", { v1: String(summary.fragmentCount) })
                          : translateUi("workflow.aiStoryboardsPending")

            return (
              <article
                key={ep.id}
                className={`drama-ep-card${planning ? ' is-planning' : ''}${
                  summary.videoRunning > 0 ? ' is-generating' : ''
                }`}
              >
                <button
                  type="button"
                  className="drama-ep-card-poster"
                  onClick={() => navigate(`/drama/projects/${projectId}/episodes/${ep.id}`)}
                  aria-label={translateUi("workflow.edit_c79457", { v1: String(ep.name) })}
                >
                  {summary.previewUrl ? (
                    <img src={summary.previewUrl} alt="" className="drama-ep-card-poster-img" />
                  ) : (
                    <div className="drama-ep-card-poster-fallback">
                      <span className="drama-ep-card-poster-vertical">
                        {verticalTitleLabel(ep.name)}
                      </span>
                    </div>
                  )}
                  <span className="drama-ep-card-ep-badge">{epLabel}</span>
                  {summary.previewUrl ? (
                    <span className="drama-ep-card-play" aria-hidden>
                      <Film size={18} strokeWidth={1.75} />
                    </span>
                  ) : null}
                  {summary.videoRunning > 0 ? (
                    <span className="drama-ep-card-busy" aria-hidden />
                  ) : null}
                </button>

                <div className="drama-ep-card-body">
                  <h3 className="drama-ep-card-title">{ep.name}</h3>
                  <div className="drama-ep-card-meta">
                    <span className="drama-ep-card-meta-item">
                      <Layers size={14} strokeWidth={1.75} aria-hidden />
                      {statusLabel}
                    </span>
                    {summary.totalSec > 0 ? (
                      <span className="drama-ep-card-meta-item">
                        <Clapperboard size={14} strokeWidth={1.75} aria-hidden />
                        {translateUi("workflow.aboutS", { v1: summary.totalSec })}</span>
                    ) : null}
                  </div>
                  {summary.fragmentCount > 0 ? (
                    <div className="drama-ep-card-progress" aria-hidden>
                      <div
                        className="drama-ep-card-progress-bar"
                        style={{ width: `${progress}%` }}
                      />
                    </div>
                  ) : null}
                </div>

                <div className="drama-ep-card-actions">
                  <button
                    type="button"
                    className="drama-ep-card-ai-btn"
                    disabled={reseeding || planningId != null}
                    onClick={() => void handlePlanEpisode(ep)}
                  >
                    <Wand2 size={15} strokeWidth={1.75} aria-hidden />
                    {planning ? translateUi("workflow.planningAiStoryboards") : translateUi("workflow.aiStoryboards")}
                  </button>
                  <button
                    type="button"
                    className="drama-btn-primary drama-ep-card-edit-btn"
                    disabled={planning}
                    onClick={() => navigate(`/drama/projects/${projectId}/episodes/${ep.id}`)}
                  >
                    {translateUi("workflow.edit")}
                  </button>
                </div>
              </article>
            )
          })}
        </div>
      )}
      <FragmentPlanSkillModal
        open={planTarget != null}
        message={translateUi("workflow.aiWillReplanTheStoryboardForReplacingItsExisting", { v1: String(planTarget?.name || '') })}
        onCancel={() => setPlanTarget(null)}
        onConfirm={(skillIds) => {
          if (planTarget) void startPlanEpisode(planTarget, skillIds)
        }}
      />
    </div>
  )
}
