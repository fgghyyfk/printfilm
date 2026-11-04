import { useI18n } from '../../../i18n'
/** 出境资产节点：缩略图卡片，可关联多个分镜 */
import { memo } from 'react'
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { X } from 'lucide-react'
import type { EpisodeAssetNodeData } from './buildEpisodeFlow'

type Props = NodeProps<Node<EpisodeAssetNodeData>> & {
  onUnlinkAsset?: (fragmentId: number, assetId: number) => void
}

// 渲染出境资产节点（同一资产全局只显示一张卡片）
function EpisodeAssetNodeComponent({ data, selected, onUnlinkAsset }: Props) {
  const { t: translateUi } = useI18n()
  const links = data.linkedFragments || []

  return (
    <div className={`ep-asset-node${selected ? ' is-selected' : ''}`}>
      <div className="ep-asset-node-head">
        <span>{data.typeLabel}</span>
        {links.length > 1 ? (
          <span className="ep-asset-node-count" title={translateUi("workflow.referencedShotCount")}>
            {translateUi("workflow.shots_1d784f", { v1: links.length })}</span>
        ) : null}
      </div>
      <div className="ep-asset-node-thumb">
        {data.previewUrl ? (
          <img src={data.previewUrl} alt="" draggable={false} />
        ) : (
          <span>{(data.name || '?')[0]}</span>
        )}
      </div>
      <div className="ep-asset-node-name">{data.name}</div>
      {selected && links.length > 0 ? (
        <div className="ep-asset-node-links nodrag nopan">
          {links.map((link) => (
            <button
              key={link.fragmentId}
              type="button"
              className="ep-asset-node-unlink-chip"
              aria-label={translateUi("workflow.unlink_cde958", { v1: String(link.label) })}
              title={translateUi("workflow.unlink_cde958", { v1: String(link.label) })}
              onClick={() => onUnlinkAsset?.(link.fragmentId, data.assetId)}
            >
              {link.label}
              <X size={11} strokeWidth={2.4} aria-hidden />
            </button>
          ))}
        </div>
      ) : null}
      <Handle className="ep-frag-handle" type="source" position={Position.Right} />
    </div>
  )
}

export const EpisodeAssetNode = memo(EpisodeAssetNodeComponent)
