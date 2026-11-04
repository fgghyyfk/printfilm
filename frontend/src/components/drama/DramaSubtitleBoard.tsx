import { useI18n } from '../../i18n'
import { translate as translateUi } from '../../i18n/runtime'
/** 分镜字幕板：预览整集口播字幕，支持折叠与导出。 */
import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import type { DramaFragment } from '../../api/drama'
import { sanitizeMediaBasename } from '../../lib/canvasNodeMedia'
import { triggerBlobDownload } from '../../lib/clientDownload'
import {
  buildDramaSubtitleBoard,
  exportDramaSubtitleBoardSrt,
  formatSubtitleClock,
  subtitleModeUsesModelOutput,
  type DramaSubtitleMode,
} from '../../lib/dramaSubtitleBoard'

type Props = {
  fragments: DramaFragment[]
  episodeName?: string
  subtitleMode: DramaSubtitleMode
}

// 渲染可折叠的分集字幕板预览与导出按钮。
export function DramaSubtitleBoard({
  fragments,
  episodeName = translateUi("workflow.thisEpisode"),
  subtitleMode,
}: Props) {
  const { t: translateUi } = useI18n()
  const cues = buildDramaSubtitleBoard(fragments)
  const modelOutput = subtitleModeUsesModelOutput(subtitleMode)
  // collapsed 默认折叠，减少右侧预览占位
  const [collapsed, setCollapsed] = useState(true)

  return (
    <section className={`drama-subtitle-board${collapsed ? ' is-collapsed' : ''}`}>
      <div className="drama-subtitle-board__header">
        <button
          type="button"
          className="drama-subtitle-board__toggle"
          aria-expanded={!collapsed}
          onClick={() => setCollapsed((prev) => !prev)}
        >
          <span className="drama-subtitle-board__title-wrap">
            <h4>{translateUi("workflow.subtitlePanel")}</h4>
            <p>
              {translateUi("workflow.lines", { v1: modelOutput ? translateUi("workflow.modelGenerated") : translateUi("workflow.addedDuringComposition"), v2: cues.length })}</p>
          </span>
          {collapsed ? (
            <ChevronDown size={16} strokeWidth={1.8} aria-hidden />
          ) : (
            <ChevronUp size={16} strokeWidth={1.8} aria-hidden />
          )}
        </button>
        <button
          type="button"
          className="drama-subtitle-board__export"
          disabled={cues.length === 0}
          title={translateUi("workflow.exportSrtForImportIntoCapcut")}
          onClick={(event) => {
            event.stopPropagation()
            const srt = exportDramaSubtitleBoardSrt(fragments)
            if (!srt) return
            // 剪映桌面版可识别 UTF-8 BOM 的 .srt
            const blob = new Blob(['\uFEFF', srt], { type: 'application/x-subrip;charset=utf-8' })
            triggerBlobDownload(blob, `${sanitizeMediaBasename(episodeName)}_字幕.srt`)
          }}
        >
          {translateUi("workflow.exportSrt")}
        </button>
      </div>
      {!collapsed ? (
        cues.length === 0 ? (
          <div className="drama-subtitle-board__empty">{translateUi("workflow.thisStoryboardHasNoDialogueOrNarrationSubtitlesTo")}</div>
        ) : (
          <div className="drama-subtitle-board__list">
            {cues.map((cue, index) => (
              <div
                key={`${cue.fragmentId}-${cue.startSec}-${index}`}
                className="drama-subtitle-board__item"
              >
                <div className="drama-subtitle-board__meta">
                  <span>
                    {formatSubtitleClock(cue.startSec)} - {formatSubtitleClock(cue.endSec)}
                  </span>
                  <span>{translateUi("workflow.clip", { v1: String(cue.fragmentIndex + 1).padStart(2, '0') })}</span>
                  <span>{cue.speaker}</span>
                </div>
                <div className="drama-subtitle-board__text">{cue.text}</div>
              </div>
            ))}
          </div>
        )
      ) : null}
    </section>
  )
}
