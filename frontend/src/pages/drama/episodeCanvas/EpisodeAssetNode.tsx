/** 出境资产节点：缩略图卡片，可关联多个分镜 */
import { memo } from 'react'
import { useTeamReadOnly } from '../../../features/teams/readOnlyContext'
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { X } from 'lucide-react'
import type { EpisodeAssetNodeData } from './buildEpisodeFlow'
import { AssetCard } from '../../../components/assets/AssetLibrary'

type Props = NodeProps<Node<EpisodeAssetNodeData>> & {
  onUnlinkAsset?: (fragmentId: number, assetId: number) => void
}

// 渲染出境资产节点（同一资产全局只显示一张卡片）
function EpisodeAssetNodeComponent({ data, selected, onUnlinkAsset }: Props) {
  const readOnly = useTeamReadOnly()
  const links = data.linkedFragments || []

  return (
    <div className={`ep-asset-node${selected ? ' is-selected' : ''}`}>
      <AssetCard asset={{ name: data.name, type: data.typeLabel, cover: data.previewUrl }} compact selected={selected} footer={`关联 ${links.length} 个分镜`}>
      {selected && links.length > 0 ? (
        <div className="ep-asset-node-links nodrag nopan">
          {links.map((link) => (
            <button
              key={link.fragmentId}
              type="button"
              className="ep-asset-node-unlink-chip"
              aria-label={`取消 ${link.label} 的关联`}
              title={`取消 ${link.label} 的关联`}
              disabled={readOnly}
              onClick={() => onUnlinkAsset?.(link.fragmentId, data.assetId)}
            >
              {link.label}
              <X size={11} strokeWidth={2.4} aria-hidden />
            </button>
          ))}
        </div>
      ) : null}
      </AssetCard>
      <Handle className="ep-frag-handle" type="source" position={Position.Right} />
    </div>
  )
}

export const EpisodeAssetNode = memo(EpisodeAssetNodeComponent)
