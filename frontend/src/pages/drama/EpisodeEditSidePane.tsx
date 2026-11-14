import { useI18n } from '../../i18n'
import { translate as translateUi } from '../../i18n/runtime'
/** 分集编辑右侧：分段视频预览 + 入口打开全屏分镜画布 */
import { useState } from 'react'
import { Download, Loader2 } from 'lucide-react'
import DramaPublishModal from '../../features/marketplace/DramaPublishModal'
import { useMarketplace } from '../../features/marketplace/hooks'
import type { DramaFragment } from '../../api/drama'
import { DramaFragmentSegmentedVideoPlayer } from '../../components/drama/DramaFragmentSegmentedVideoPlayer'
import { triggerBlobDownload } from '../../lib/clientDownload'
import {
  composeEpisodeVideoClient,
  episodeComposeFilename,
  listEpisodeComposeClips,
  type EpisodeComposeProgress,
} from '../../lib/composeEpisodeVideoClient'
import { dialog } from '../../lib/dialog'

type Props = {
  fragments: DramaFragment[]
  playingFragmentId: number | null
  onPlayingFragmentChange: (fragmentId: number) => void
  aspectRatio: string
  episodeId?: number
  projectId?: string
  episodeName?: string
  onOpenStoryboard: () => void
  /** 预览历史版本时覆盖当前镜 video src */
  previewVideoUrl?: string | null
  previewPosterUrl?: string | null
  previewLabel?: string
  onClearPreview?: () => void
  onActivatePreview?: () => void
}

/** 把合成进度转成按钮文案 */
function composeProgressLabel(progress: EpisodeComposeProgress | null, busy: boolean) {
  if (!busy) return translateUi("workflow.composeAndDownloadEpisode")
  if (!progress) return translateUi("workflow.composingEpisode")
  if (progress.phase === 'download') return translateUi("workflow.fetchingClip", { v1: String(progress.done), v2: String(progress.total) })
  if (progress.phase === 'server') return translateUi("workflow.reEncodingAndComposingOnTheServer")
  return translateUi("workflow.composing")
}

// 渲染分集右侧预览与画布入口
export function EpisodeEditSidePane({
  fragments,
  playingFragmentId,
  onPlayingFragmentChange,
  aspectRatio,
  episodeId,
  projectId,
  episodeName = translateUi("workflow.thisEpisode"),
  onOpenStoryboard,
  previewVideoUrl = null,
  previewPosterUrl = null,
  previewLabel = '',
  onClearPreview,
  onActivatePreview,
}: Props) {
  const { t: translateUi } = useI18n()
  const hasSelection = playingFragmentId !== null
  /*
   * composeBusy 本地拼接中
   * composeProgress 拉取/拼接进度
   * composeError 失败原因
   */
  const { capabilities } = useMarketplace()
  const [publishOpen, setPublishOpen] = useState(false)
  const [composeBusy, setComposeBusy] = useState(false)
  const [composeProgress, setComposeProgress] = useState<EpisodeComposeProgress | null>(null)
  const [composeError, setComposeError] = useState('')
  const composeClips = listEpisodeComposeClips(fragments)
  const missingCount = fragments.length - composeClips.length

  // 浏览器内拼接已生成镜头并下载成片
  async function handleComposeDownload() {
    if (composeBusy || composeClips.length === 0) return
    if (missingCount > 0) {
      const ok = await dialog.confirm({
        title: translateUi("workflow.someStoryboardClipsAreMissing"),
        message: translateUi("workflow.shotsHaveNoVideoComposeOnlyTheCompletedClips", { v1: String(missingCount), v2: String(composeClips.length) }),
        confirmText: translateUi("workflow.continueComposing"),
      })
      if (!ok) return
    }
    setComposeError('')
    setComposeBusy(true)
    setComposeProgress({ phase: 'download', done: 0, total: composeClips.length })
    try {
      const blob = await composeEpisodeVideoClient(composeClips, setComposeProgress, {
        episodeId,
      })
      triggerBlobDownload(blob, episodeComposeFilename(episodeName))
    } catch (err) {
      setComposeError(err instanceof Error ? err.message : translateUi("workflow.couldNotComposeTheFullVideo"))
    } finally {
      setComposeBusy(false)
      setComposeProgress(null)
    }
  }

  return (
    <aside className="drama-ep-preview">
      <div className="drama-ep-side-header">
        <div className="drama-ep-side-tabs" role="tablist" aria-label={translateUi("workflow.sidePanel")}>
          <button type="button" role="tab" aria-selected className="active">
            {translateUi("ecomSet.result.preview")}
          </button>
          <button type="button" role="tab" onClick={onOpenStoryboard}>
            {translateUi("workflow.canvas")}
          </button>
        </div>
        <button
          type="button"
          className="drama-ep-compose-btn drama-ep-compose-btn--header"
          disabled={composeBusy || composeClips.length === 0}
          title={
            composeClips.length === 0
              ? translateUi("workflow.generateStoryboardClipsFirst")
              : translateUi("workflow.composeThisEpisodeSCompletedClipsInYourBrowser")
          }
          onClick={() => void handleComposeDownload()}
        >
          {composeBusy ? (
            <Loader2 size={14} className="drama-ep-compose-spin" />
          ) : (
            <Download size={14} strokeWidth={1.8} />
          )}
          {composeProgressLabel(composeProgress, composeBusy)}
        </button>
        {capabilities?.enabled && projectId && episodeId ? (
          <button
            type="button"
            className="drama-ep-compose-btn drama-ep-compose-btn--header"
            onClick={() => setPublishOpen(true)}
          >
            发布到数字资产市场
          </button>
        ) : null}
      </div>
      {composeError ? <p className="drama-ep-compose-error drama-ep-compose-error--header">{composeError}</p> : null}

      {previewVideoUrl ? (
        <div className="drama-ep-preview-banner">
          <span>{translateUi("workflow.previewHistoricalVersion", { v1: previewLabel ? ` · ${previewLabel}` : '' })}</span>
          <div className="drama-ep-preview-banner-actions">
            {onActivatePreview ? (
              <button type="button" className="drama-ep-preview-banner-btn" onClick={onActivatePreview}>
                {translateUi("workflow.makeCurrent")}
              </button>
            ) : null}
            {onClearPreview ? (
              <button type="button" className="drama-ep-preview-banner-btn is-ghost" onClick={onClearPreview}>
                {translateUi("workflow.exitPreview")}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}

      {!hasSelection || fragments.length === 0 ? (
        <p className="drama-ep-empty">{translateUi("workflow.selectAShotBelow")}</p>
      ) : (
        <div className="drama-ep-preview-inner">
          <DramaFragmentSegmentedVideoPlayer
            fragments={fragments}
            playingFragmentId={playingFragmentId}
            onPlayingFragmentChange={onPlayingFragmentChange}
            aspectRatio={aspectRatio}
            overrideVideoUrl={previewVideoUrl}
            overridePosterUrl={previewPosterUrl}
          />
          {!fragments.some((f) => f.video) && (
            <button type="button" className="drama-ep-open-canvas" onClick={onOpenStoryboard}>
              {translateUi("workflow.openStoryboardCanvas_96778f")}
            </button>
          )}
        </div>
      )}
      {publishOpen && capabilities?.enabled && projectId && <DramaPublishModal projectId={Number(projectId)} initialEpisodeId={episodeId} onClose={() => setPublishOpen(false)} />}
    </aside>
  )
}
