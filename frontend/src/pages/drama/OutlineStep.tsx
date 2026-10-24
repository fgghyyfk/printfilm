/** 剧情大纲步骤：自动摘要 + 分集剧本流水线 */
import { isRequestAborted } from '../../api/http'
import { useEffect, useRef, useState } from 'react'
import {
  dramaApi,
  type DramaProject,
  type DramaScript,
} from '../../api/drama'
import { getImageStyleLabel, type ImageStyleId } from '../../lib/dramaImageStyles'
import {
  autoMissingEpisodeCount,
  getAssetsSeedStatus,
  getEpisodeContentStatus,
  getImageStyleId,
  getSummaryStatus,
  hasSubstantialEpisode,
  parseEpisodeBodies,
} from './dramaWorkspaceUtils'
import { dialog } from '../../lib/dialog'
import Modal from '../../components/ui/Modal'
import { DramaImageStyleModal } from './DramaImageStyleModal'
import { DramaProjectSettingsModal } from './DramaProjectSettingsModal'
import { OutlineEpisodePanel } from './OutlineEpisodePanel'
import { useTeamEditLock } from '../../features/teams/useTeamEditLock'

type MetaModalKey = 'source' | 'summary' | 'project'

export type OutlineStepProps = {
  projectId: number
  project: DramaProject
  onProjectChange: (p: DramaProject) => void
  /** 可选：大纲就绪回调（工作区已不再依赖「下一步」） */
  onOutlineReadyChange?: (ready: boolean) => void
  onError: (msg: string) => void
}

// 渲染剧情大纲步骤
export function OutlineStep({
  projectId,
  project,
  onProjectChange,
  onOutlineReadyChange,
  onError,
}: OutlineStepProps) {
  const notifyOutlineReady = (ready: boolean) => onOutlineReadyChange?.(ready)
  const [script, setScript] = useState<DramaScript | null>(project.script || null)
  const [metaModal, setMetaModal] = useState<MetaModalKey | null>(null)
  // 进页即按服务端状态展示，避免摘要/分集生成中外部一片空白
  const [summaryGenerating, setSummaryGenerating] = useState(
    () => getSummaryStatus(project.script || null) === 'generating',
  )
  const [assetsSeeding, setAssetsSeeding] = useState(
    () => getAssetsSeedStatus(project) === 'generating',
  )
  const [episodeGenerating, setEpisodeGenerating] = useState(
    () => getEpisodeContentStatus(project.script || null) === 'generating',
  )
  const [summaryError, setSummaryError] = useState('')
  const [episodeError, setEpisodeError] = useState('')
  const [progress, setProgress] = useState(() => {
    const prog = (project.script?.params || {}).episode_content_progress as
      | { done?: number; total?: number }
      | undefined
    return {
      done: Number(prog?.done) || 0,
      total: Number(prog?.total) || 0,
    }
  })
  const [summaryEditing, setSummaryEditing] = useState(false)
  const [summaryDraft, setSummaryDraft] = useState<Record<string, unknown> | null>(null)
  const [summarySaving, setSummarySaving] = useState(false)
  const [sourceEditing, setSourceEditing] = useState(false)
  const [sourceDraft, setSourceDraft] = useState('')
  const [sourceSaving, setSourceSaving] = useState(false)
  const scriptLock = useTeamEditLock({ projectId, type: 'script', resourceId: 0 }, summaryEditing || sourceEditing, async (isCurrent) => { const fresh = await dramaApi.getScript(projectId); if (isCurrent()) { setScript(fresh); if (summaryEditing) setSummaryDraft(fresh.summary || {}); if (sourceEditing) setSourceDraft(fresh.source || '') } })
  const pipelineRef = useRef(0)
  const projectRef = useRef(project)
  projectRef.current = project

  const summary = (script?.summary || null) as Record<string, unknown> | null
  const summaryStatus = getSummaryStatus(script)
  const episodeStatus = getEpisodeContentStatus(script)
  const assetsSeedStatus = getAssetsSeedStatus(project)
  const episodeCount =
    Number(
      summary?.episodeCount ||
        (script?.params || {}).episode_count ||
        project.params?.episode_count,
    ) || 0
  const imageStyleId = getImageStyleId(script, project)

  function openSourceModal() {
    setSourceEditing(false)
    setSourceDraft(script?.source || '')
    setMetaModal('source')
  }

  function openSummaryModal() {
    setSummaryEditing(false)
    setMetaModal('summary')
  }

  function closeMetaModal() {
    setMetaModal(null)
    setSourceEditing(false)
    setSummaryEditing(false)
  }

  // 轮询剧本直到条件满足（worker 异步任务）
  async function pollScriptUntil(
    predicate: (s: DramaScript) => boolean,
    runToken?: number,
  ): Promise<DramaScript | null> {
    const started = Date.now()
    while (Date.now() - started < 45 * 60 * 1000) {
      if (runToken !== undefined && runToken !== pipelineRef.current) return null
      const s = await dramaApi.getScript(projectId)
      setScript(s)
      const prog = (s.params || {}).episode_content_progress as
        | { done?: number; total?: number }
        | undefined
      if (prog && (prog.total || prog.done)) {
        setProgress({
          done: Number(prog.done) || 0,
          total: Number(prog.total) || 0,
        })
      }
      if (predicate(s)) return s
      await new Promise((r) => setTimeout(r, 2000))
    }
    throw new Error('等待生成超时，请刷新后重试')
  }

  // 入队并等待剧本摘要完成
  async function runSummary(runToken?: number) {
    setSummaryGenerating(true)
    setSummaryError('')
    try {
      const res = await dramaApi.scriptSummary({ project_id: projectId })
      setScript(res.script)
      const finished = await pollScriptUntil((s) => {
        const st = getSummaryStatus(s)
        return st === 'completed' || st === 'failed'
      }, runToken)
      if (!finished) return null
      if (getSummaryStatus(finished) === 'failed') {
        const msg =
          String((finished.params || {}).summary_error || '') || '剧本摘要生成失败'
        setSummaryError(msg)
        onError(msg)
        throw new Error(msg)
      }
      const p = await dramaApi.getProject(projectId)
      onProjectChange(p)
      return finished
    } catch (err) {
      if (isRequestAborted(err)) throw err
      const msg = err instanceof Error ? err.message : '剧本摘要生成失败'
      setSummaryError(msg)
      onError(msg)
      throw err
    } finally {
      setSummaryGenerating(false)
    }
  }

  // 摘要完成后等待/触发资产抽取（后端摘要任务也会自动抽；此处兜底）
  async function ensureAssetsSeeded(runToken?: number): Promise<boolean> {
    setAssetsSeeding(true)
    try {
      let p = await dramaApi.getProject(projectId)
      onProjectChange(p)
      let st = getAssetsSeedStatus(p)
      if (st !== 'generating' && st !== 'done') {
        try {
          await dramaApi.seedAssets(projectId)
        } catch {
          /* 可能已在后台抽取，继续轮询 */
        }
      }
      const started = Date.now()
      while (Date.now() - started < 10 * 60 * 1000) {
        if (runToken !== undefined && runToken !== pipelineRef.current) return false
        p = await dramaApi.getProject(projectId)
        onProjectChange(p)
        st = getAssetsSeedStatus(p)
        if (st === 'done' || st === 'failed') {
          setAssetsSeeding(false)
          return st === 'done' || st === 'failed'
        }
        setAssetsSeeding(st === 'generating' || !st)
        await new Promise((r) => setTimeout(r, 2000))
      }
      setAssetsSeeding(false)
      return true
    } catch {
      setAssetsSeeding(false)
      return true
    }
  }

  // 入队并轮询分集剧本直到完成（摘要任务成功后会自动入队；此处仅兜底）
  async function runEpisodeScripts(runToken?: number, force = false) {
    setEpisodeGenerating(true)
    setEpisodeError('')
    try {
      const current = await dramaApi.getScript(projectId)
      setScript(current)
      const already = getEpisodeContentStatus(current)
      if (already !== 'generating' || force) {
        const res = await dramaApi.episodeScript({
          project_id: projectId,
          batch_size: 1,
          force,
        })
        setScript(res.script)
        setProgress({ done: res.total_generated, total: res.total_target })
      }
      const finished = await pollScriptUntil((s) => {
        const st = getEpisodeContentStatus(s)
        return st === 'completed' || st === 'failed'
      }, runToken)
      if (!finished) return
      if (getEpisodeContentStatus(finished) === 'failed') {
        const msg =
          String((finished.params || {}).episode_content_error || '') ||
          '分集剧本生成失败'
        setEpisodeError(msg)
        onError(msg)
        notifyOutlineReady(false)
        return
      }
      notifyOutlineReady(true)
      const p = await dramaApi.getProject(projectId)
      onProjectChange(p)
    } catch (err) {
      if (isRequestAborted(err)) return
      const msg = err instanceof Error ? err.message : '分集剧本生成失败'
      setEpisodeError(msg)
      onError(msg)
      notifyOutlineReady(false)
    } finally {
      setEpisodeGenerating(false)
    }
  }

  // 手动强制重写全部分集正文
  async function handleRegenerateEpisodes() {
    if (episodeGenerating) return
    const ok = await dialog.confirm({
      title: '重新生成分集剧本',
      message:
        '将清空当前分集正文并按最新剧本摘要重新生成全部集数，已有编辑将丢失。是否继续？',
      confirmText: '重新生成',
      tone: 'danger',
    })
    if (!ok) return
    notifyOutlineReady(false)
    await runEpisodeScripts(undefined, true)
  }

  // 挂载流水线：整剧摘要 → 抽资产 → 分集剧本（分镜由后端在分集完成后入队）
  useEffect(() => {
    const token = ++pipelineRef.current
    async function pipeline() {
      let current = script
      let status = getSummaryStatus(current)

      // 1) 必须先完成整剧摘要；失败且尚未进入分集则停
      if (status !== 'completed') {
        const epStatusEarly = getEpisodeContentStatus(current)
        const bodiesEarly = parseEpisodeBodies(current)
        if (status === 'failed') {
          const msg =
            String((current?.params || {}).summary_error || '') || '剧本摘要生成失败'
          setSummaryError(msg)
          onError(msg)
          // 分集已在生成/已有正文时继续跟进度，不因摘要失败吞掉整剧进度
          if (
            epStatusEarly !== 'generating' &&
            epStatusEarly !== 'completed' &&
            !hasSubstantialEpisode(bodiesEarly)
          ) {
            setEpisodeGenerating(false)
            return
          }
        } else {
          try {
            if (status === 'generating') {
              setSummaryGenerating(true)
              current = await pollScriptUntil((s) => {
                const st = getSummaryStatus(s)
                return st === 'completed' || st === 'failed'
              }, token)
              setSummaryGenerating(false)
            } else {
              current = await runSummary(token)
            }
          } catch {
            setSummaryGenerating(false)
            return
          }
          if (!current || token !== pipelineRef.current) return
          status = getSummaryStatus(current)
          if (status === 'failed') {
            const msg =
              String((current.params || {}).summary_error || '') || '剧本摘要生成失败'
            setSummaryError(msg)
            onError(msg)
            setEpisodeGenerating(false)
            return
          }
          if (status !== 'completed') return
        }
      }

      // 2) 摘要完成后抽取资产（后端摘要任务也会自动抽，这里等待/兜底）
      if (token !== pipelineRef.current) return
      await ensureAssetsSeeded(token)
      if (token !== pipelineRef.current) return

      const target =
        Number(
          (current?.summary as Record<string, unknown> | null)?.episodeCount ||
            (current?.params || {}).episode_count ||
            projectRef.current.params?.episode_count,
        ) || 0
      current = await dramaApi.getScript(projectId)
      setScript(current)
      const bodies = parseEpisodeBodies(current)
      const epStatus = getEpisodeContentStatus(current)
      const autoMissing = autoMissingEpisodeCount(bodies, target)
      const complete =
        epStatus !== 'failed' && autoMissing === 0 && hasSubstantialEpisode(bodies)
      if (complete) {
        notifyOutlineReady(true)
        return
      }

      // 3) 分集剧本：已在生成则只轮询；否则兜底入队
      if (epStatus === 'generating') {
        setEpisodeGenerating(true)
        const finished = await pollScriptUntil((s) => {
          const st = getEpisodeContentStatus(s)
          return st === 'completed' || st === 'failed'
        }, token)
        setEpisodeGenerating(false)
        if (!finished) return
        if (getEpisodeContentStatus(finished) === 'failed') {
          const msg =
            String((finished.params || {}).episode_content_error || '') ||
            '分集剧本生成失败'
          setEpisodeError(msg)
          onError(msg)
          notifyOutlineReady(false)
          return
        }
        notifyOutlineReady(true)
        const p = await dramaApi.getProject(projectId)
        onProjectChange(p)
        return
      }
      await runEpisodeScripts(token)
    }
    void pipeline()
    return () => {
      pipelineRef.current += 1
      notifyOutlineReady(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  // 更新画面风格
  async function handleStyleChange(styleId: string) {
    try {
      const updated = await dramaApi.updateScript(projectId, {
        image_style_id: styleId || undefined,
      })
      setScript(updated)
      const p = await dramaApi.getProject(projectId)
      onProjectChange(p)
    } catch (err) {
      if (isRequestAborted(err)) return
      onError(err instanceof Error ? err.message : '风格更新失败')
    }
  }

  // 进入剧本摘要编辑
  function startSummaryEdit() {
    if (!summary) return
    setSummaryDraft(structuredClone(summary) as Record<string, unknown>)
    setSummaryEditing(true)
  }

  // 保存手动编辑的剧本摘要
  async function saveSummaryEdit() {
    if (!summaryDraft) return
    setSummarySaving(true)
    try {
      const updated = await dramaApi.updateScript(projectId, { summary: summaryDraft })
      setScript(updated)
      setSummaryEditing(false)
      setSummaryDraft(null)
      const p = await dramaApi.getProject(projectId)
      onProjectChange(p)
    } catch (err) {
      if (isRequestAborted(err)) return
      onError(err instanceof Error ? err.message : '摘要保存失败')
    } finally {
      setSummarySaving(false)
    }
  }

  const characters = Array.isArray(summary?.characters)
    ? (summary.characters as Array<Record<string, unknown>>)
    : []

  async function saveSourceEdit() {
    setSourceSaving(true)
    try {
      const updated = await dramaApi.updateScript(projectId, { source: sourceDraft })
      setScript(updated)
      setSourceEditing(false)
    } catch (err) {
      if (isRequestAborted(err)) return
      onError(err instanceof Error ? err.message : '创意保存失败')
    } finally {
      setSourceSaving(false)
    }
  }

  return (
    <div className="drama-outline-page">
      <div className="drama-outline-toolbar">
        <div className="drama-outline-toolbar-left">
          <DramaImageStyleModal
            variant="field"
            fieldLabel="项目风格"
            title="选择项目风格"
            emptyLabel="未选择"
            value={(imageStyleId as ImageStyleId | '') || ''}
            onChange={(id) => void handleStyleChange(id)}
          />
          <span className="drama-outline-toolbar-meta">
            {episodeCount ? `共 ${episodeCount} 集` : '集数待定'}
            {summaryGenerating || summaryStatus === 'generating'
              ? ' · 全剧摘要生成中…'
              : assetsSeeding || assetsSeedStatus === 'generating'
                ? ' · 抽取资产中…'
                : episodeGenerating || episodeStatus === 'generating'
                  ? progress.total > 0
                    ? ` · 整剧进度 ${progress.done}/${progress.total}`
                    : ' · 分集剧本生成中…'
                  : ''}
          </span>
        </div>
        <div className="drama-outline-toolbar-actions">
          <button
            type="button"
            className={`drama-outline-chip-btn${metaModal === 'source' ? ' is-on' : ''}`}
            onClick={openSourceModal}
          >
            整剧创意
          </button>
          <button
            type="button"
            className={`drama-outline-chip-btn${metaModal === 'project' ? ' is-on' : ''}`}
            onClick={() => setMetaModal('project')}
          >
            项目设置
          </button>
          <button
            type="button"
            className={`drama-outline-chip-btn${metaModal === 'summary' ? ' is-on' : ''}`}
            onClick={openSummaryModal}
          >
            全局设定
          </button>
          {(episodeError || episodeStatus === 'failed') && (
            <button type="button" className="drama-outline-chip-btn" onClick={() => void handleRegenerateEpisodes()}>
              重跑全部分集
            </button>
          )}
        </div>
      </div>

      <Modal
        open={metaModal === 'source'}
        onClose={closeMetaModal}
        title="整剧创意"
        size="lg"
        className="drama-outline-meta-modal"
        footer={
          sourceEditing ? (
            <>
              <button type="button" className="drama-btn-ghost" onClick={() => setSourceEditing(false)}>
                取消编辑
              </button>
              <button
                type="button"
                className="drama-btn-primary"
                disabled={sourceSaving || scriptLock.readOnly}
                onClick={() => void saveSourceEdit()}
              >
                {sourceSaving ? '保存中…' : '保存'}
              </button>
            </>
          ) : (
            <button
              type="button"
              className="drama-btn-ghost"
              onClick={() => {
                setSourceDraft(script?.source || '')
                setSourceEditing(true)
              }}
            >
              编辑
            </button>
          )
        }
      >
        <div className="drama-outline-meta-modal-body">
          {scriptLock.readOnly && <p className="pf-team-alert" role="status">{scriptLock.checking ? '正在取得编辑占用…' : scriptLock.error}<button type="button" onClick={scriptLock.retry}>重新取得编辑权并刷新</button></p>}
        {sourceEditing ? (
            <textarea
              className="drama-ep-section-textarea"
              rows={16}
              value={sourceDraft}
              onChange={(e) => setSourceDraft(e.target.value)}
            />
          ) : (
            <p className="drama-pre">{script?.source?.trim() || '暂无整剧创意'}</p>
          )}
        </div>
      </Modal>

      <Modal
        open={metaModal === 'summary'}
        onClose={closeMetaModal}
        title="全局设定"
        size="lg"
        className="drama-outline-meta-modal"
        footer={
          summaryEditing ? (
            <>
              <button type="button" className="drama-btn-ghost" onClick={() => setSummaryEditing(false)}>
                取消编辑
              </button>
              <button
                type="button"
                className="drama-btn-primary"
                disabled={summarySaving || scriptLock.readOnly}
                onClick={() => void saveSummaryEdit()}
              >
                {summarySaving ? '保存中…' : '保存'}
              </button>
            </>
          ) : summary && summaryStatus === 'completed' ? (
            <button type="button" className="drama-btn-ghost" onClick={() => startSummaryEdit()}>
              编辑
            </button>
          ) : summaryError || summaryStatus === 'failed' ? (
            <button type="button" className="drama-btn-primary" onClick={() => void runSummary()}>
              重新生成
            </button>
          ) : null
        }
      >
        <div className="drama-outline-meta-modal-body">
          {scriptLock.readOnly && <p className="pf-team-alert" role="status">{scriptLock.checking ? '正在取得编辑占用…' : scriptLock.error}<button type="button" onClick={scriptLock.retry}>重新取得编辑权并刷新</button></p>}
          {summaryGenerating || summaryStatus === 'generating' ? (
            <p className="drama-loader">全剧摘要生成中…</p>
          ) : null}
          {summaryError || summaryStatus === 'failed' ? (
            <p className="drama-error">{summaryError || '摘要生成失败'}</p>
          ) : null}
          {summaryEditing && summaryDraft ? (
            <div className="drama-outline-edit">
              <EditableSummaryField
                label="剧名"
                value={String(summaryDraft.seriesTitle || '')}
                onChange={(v) =>
                  setSummaryDraft((prev) => (prev ? { ...prev, seriesTitle: v } : prev))
                }
              />
              <EditableSummaryField
                label="自定义集数"
                value={String(summaryDraft.episodeCount ?? '')}
                onChange={(v) =>
                  setSummaryDraft((prev) =>
                    prev ? { ...prev, episodeCount: v ? Number(v) || v : '' } : prev,
                  )
                }
              />
              <EditableSummaryField
                label="故事类型"
                value={String(summaryDraft.storyType || '')}
                onChange={(v) => setSummaryDraft((prev) => (prev ? { ...prev, storyType: v } : prev))}
              />
              <EditableSummaryField
                label="一句话故事"
                value={String(summaryDraft.oneLineStory || '')}
                onChange={(v) =>
                  setSummaryDraft((prev) => (prev ? { ...prev, oneLineStory: v } : prev))
                }
                multiline
              />
              <EditableSummaryField
                label="故事梗概"
                value={String(summaryDraft.synopsis || '')}
                onChange={(v) => setSummaryDraft((prev) => (prev ? { ...prev, synopsis: v } : prev))}
                multiline
              />
            </div>
          ) : null}
          {!summaryEditing && summary && summaryStatus === 'completed' ? (
            <div className="drama-summary-structured">
              <SummaryField label="剧名" value={String(summary.seriesTitle || '')} />
              <SummaryField label="故事类型" value={String(summary.storyType || '')} />
              <SummaryField label="一句话故事" value={String(summary.oneLineStory || '')} />
              <SummaryField label="故事梗概" value={String(summary.synopsis || '')} />
              {characters.length > 0 ? (
                <section>
                  <h4>人物</h4>
                  <p className="drama-muted">
                    {characters.map((ch) => String(ch.name || '')).filter(Boolean).join('、')}
                  </p>
                </section>
              ) : null}
            </div>
          ) : null}
          {!summaryEditing && !summary && summaryStatus !== 'generating' && !summaryError ? (
            <p className="drama-muted">暂无全局设定，等待摘要生成或点击重新生成。</p>
          ) : null}
        </div>
      </Modal>

      <DramaProjectSettingsModal
        open={metaModal === 'project'}
        projectId={projectId}
        project={project}
        script={script}
        onClose={closeMetaModal}
        onProjectChange={onProjectChange}
        onScriptChange={setScript}
        onError={onError}
      />

      {/* 外部整剧进度：摘要 → 资产 → 分集，主界面可见 */}
      {summaryGenerating ||
      summaryStatus === 'generating' ||
      assetsSeeding ||
      assetsSeedStatus === 'generating' ||
      episodeGenerating ||
      episodeStatus === 'generating' ? (
        <div className="drama-outline-pipeline" role="status" aria-live="polite">
          <div className="drama-outline-pipeline-main">
            <span className="drama-outline-pipeline-pulse" aria-hidden />
            <div>
              <strong>
                {summaryGenerating || summaryStatus === 'generating'
                  ? '全剧摘要生成中…'
                  : assetsSeeding || assetsSeedStatus === 'generating'
                    ? '抽取资产中…'
                    : progress.total > 0
                      ? `分集剧本生成中 · 整剧进度 ${progress.done}/${progress.total}`
                      : '分集剧本生成中…'}
              </strong>
              <p>
                {summaryGenerating || summaryStatus === 'generating'
                  ? '顺序：整剧摘要 → 抽取资产 → 分集剧本 → 每集立刻 AI 分镜'
                  : assetsSeeding || assetsSeedStatus === 'generating'
                    ? '正在从整剧摘要抽取角色/场景到资产库，完成后开始生成各集剧本'
                    : '每集剧本完成后立即执行 AI 分镜；左侧可看「AI 分镜中…」状态'}
              </p>
            </div>
          </div>
          {!(summaryGenerating || summaryStatus === 'generating') &&
          !(assetsSeeding || assetsSeedStatus === 'generating') &&
          progress.total > 0 ? (
            <div className="drama-outline-pipeline-bar" aria-hidden>
              <div
                className="drama-outline-pipeline-bar-fill"
                style={{
                  width: `${Math.min(100, Math.round((100 * progress.done) / progress.total))}%`,
                }}
              />
            </div>
          ) : (
            <div className="drama-outline-pipeline-bar is-indeterminate" aria-hidden>
              <div className="drama-outline-pipeline-bar-fill" />
            </div>
          )}
        </div>
      ) : null}

      <OutlineEpisodePanel
        projectId={projectId}
        script={script}
        episodeCount={episodeCount}
        summaryReady={summaryStatus === 'completed'}
        summaryGenerating={summaryGenerating || summaryStatus === 'generating'}
        assetsSeeding={assetsSeeding || assetsSeedStatus === 'generating'}
        episodeGenerating={episodeGenerating || episodeStatus === 'generating'}
        episodeProgress={progress}
        imageStyleLabel={getImageStyleLabel(imageStyleId) || undefined}
        storyType={String(summary?.storyType || '') || undefined}
        onScriptChange={setScript}
        onProjectChange={onProjectChange}
        onError={onError}
        onOpenEpisodes={() => undefined}
      />
    </div>
  )
}

function SummaryField({ label, value }: { label: string; value: string }) {
  if (!value) return null
  return (
    <section className="drama-summary-field">
      <h4>{label}</h4>
      <p className="drama-pre">{value}</p>
    </section>
  )
}

function EditableSummaryField({
  label,
  value,
  onChange,
  multiline = false,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  multiline?: boolean
}) {
  return (
    <label className="drama-outline-edit-field">
      <span>{label}</span>
      {multiline ? (
        <textarea rows={3} value={value} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input type="text" value={value} onChange={(e) => onChange(e.target.value)} />
      )}
    </label>
  )
}
