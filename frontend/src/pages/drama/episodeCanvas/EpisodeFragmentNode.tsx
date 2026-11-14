import { useI18n } from '../../../i18n'
/** 分镜故事板节点：上方视频，下方提示词；出境资产通过左侧节点连线关联 */
import { memo, useCallback, type ChangeEvent } from 'react'
import { useTeamReadOnly } from '../../../features/teams/readOnlyContext'
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { Clapperboard, Plus } from 'lucide-react'
import type { EpisodeFragmentNodeData } from './buildEpisodeFlow'

type Props = NodeProps<Node<EpisodeFragmentNodeData>> & {
  onPromptChange?: (fragmentId: number, content: string) => void
  onRequestLinkAsset?: (fragmentId: number) => void
}

// 渲染单个分镜视频节点
function EpisodeFragmentNodeComponent({
  data,
  selected,
  onPromptChange,
  onRequestLinkAsset,
}: Props) {
  const { t: translateUi } = useI18n()
  const readOnly = useTeamReadOnly()
  const hasVideo = Boolean(data.videoUrl)
  const media = data.coverUrl || data.videoUrl

  const handlePromptChange = useCallback(
    (event: ChangeEvent<HTMLTextAreaElement>) => {
      onPromptChange?.(data.fragmentId, event.target.value)
    },
    [data.fragmentId, onPromptChange],
  )

  return (
    <div className={`ep-frag-node${selected ? ' is-selected' : ''}`}>
      <div className="ep-frag-node-head">
        <Clapperboard size={14} strokeWidth={1.8} aria-hidden />
        <span>{data.label}</span>
        {selected ? (
          <button
            type="button"
            className="ep-frag-link-btn nodrag nopan"
            title={translateUi("workflow.assetsAppearingInThisShot")}
            aria-label={translateUi("workflow.assetsAppearingInThisShot")}
            disabled={readOnly}
            onClick={() => onRequestLinkAsset?.(data.fragmentId)}
          >
            <Plus size={14} strokeWidth={2} />
          </button>
        ) : (
          <em className="ep-frag-link-count">{translateUi("workflow.assets", { v1: data.linkedCount || 0 })}</em>
        )}
      </div>

      <div className="ep-frag-media">
        {hasVideo ? (
          <video
            className="ep-frag-video"
            src={data.videoUrl}
            poster={data.coverUrl || undefined}
            controls
            playsInline
            preload="metadata"
          />
        ) : media ? (
          <img src={media} alt="" draggable={false} />
        ) : (
          <div className="ep-frag-media-empty">
            <span>{translateUi("workflow.noFinishedVideoYet")}</span>
            <small>{translateUi("workflow.generatedResultsAppearHere")}</small>
          </div>
        )}
      </div>

      <div className="ep-frag-prompt">
        <label>{translateUi("workflow.prompt")}</label>
        {selected ? (
          <textarea
            className="ep-frag-prompt-input nodrag nowheel"
            value={data.content}
            readOnly={readOnly}
            onChange={handlePromptChange}
            placeholder={translateUi("workflow.storyboardScriptSeedancePrompt")}
            rows={5}
          />
        ) : (
          <p className="ep-frag-prompt-text">
            {(data.content || '').trim() || translateUi("workflow.emptyPrompt")}
          </p>
        )}
      </div>

      <Handle
        className="ep-frag-handle"
        type="target"
        position={Position.Left}
        id="assets"
        style={{ top: '62%' }}
      />
      <Handle
        className="ep-frag-handle"
        type="target"
        position={Position.Left}
        id="seq-in-l"
        style={{ top: '38%' }}
      />
      <Handle className="ep-frag-handle" type="source" position={Position.Right} id="seq-out-r" />
      <Handle className="ep-frag-handle" type="target" position={Position.Top} id="seq-in-t" />
      <Handle className="ep-frag-handle" type="source" position={Position.Bottom} id="seq-out-b" />
    </div>
  )
}

export const EpisodeFragmentNode = memo(EpisodeFragmentNodeComponent)
