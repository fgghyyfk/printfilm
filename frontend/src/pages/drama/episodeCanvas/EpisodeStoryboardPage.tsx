import { useI18n } from '../../../i18n'
import { translate as translateMessage } from '../../../i18n/runtime'
import { MyTasksButton } from '../../../components/MyTasks'
import { TeamEpisodeEditingBoundary } from '../../../features/teams/TeamEditingBoundary'
import { useTeamReadOnly } from '../../../features/teams/readOnlyContext'
/** 分集分镜故事板全屏画布：按分镜连线，节点含视频 / 出境资产 / 提示词 */
import { createFragmentAutosave, type FragmentSaveState } from '../../../lib/dramaFragmentAutosave'
import { useFragmentNavigationGuard } from '../../../lib/useFragmentNavigationGuard'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Edge,
  type Node,
  type NodeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { ChevronLeft } from 'lucide-react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  dramaApi,
  resolveDramaAssetPreviewUrl,
  type DramaAsset,
  type DramaEpisode,
  type DramaFragment,
} from '../../../api/drama'
import Modal from '../../../components/ui/Modal'
import {
  collectFragmentAssetIds,
  normalizeAssetTab,
} from '../dramaEpisodeEditUtils'
import RequireAuth from '../RequireAuth'
import {
  buildEpisodeFragmentFlow,
  type EpisodeFragmentNodeData,
  type EpisodeFlowNodeData,
} from './buildEpisodeFlow'
import { EpisodeAssetNode } from './EpisodeAssetNode'
import { EpisodeFragmentNode } from './EpisodeFragmentNode'
import '../drama.css'
import './episodeCanvas.css'

const SAVE_DEBOUNCE_MS = 800

// 正文补上 @asset 提及
function ensureAssetMention(content: string, assetId: number): string {
  const token = `@asset:${assetId}`
  if ((content || '').includes(token)) return content || ''
  const trimmed = (content || '').trimEnd()
  return trimmed ? `${trimmed} ${token}` : token
}

// 正文去掉 @asset 提及
function removeAssetMention(content: string, assetId: number): string {
  return (content || '')
    .replace(new RegExp(`\\s*@asset:${assetId}\\b`, 'g'), ' ')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

// 鉴权后进入分镜故事板
export default function EpisodeStoryboardPage() {
  return (
    <RequireAuth>
      <ReactFlowProvider>
        <TeamEpisodeEditingBoundary><EpisodeStoryboardInner /></TeamEpisodeEditingBoundary>
      </ReactFlowProvider>
    </RequireAuth>
  )
}

// 加载分集并渲染全屏故事板
function EpisodeStoryboardInner() {
  const { t: translateUi } = useI18n()
  const readOnly = useTeamReadOnly()
  const readOnlyRef = useRef(readOnly)
  readOnlyRef.current = readOnly
  const { projectId, episodeId } = useParams()
  const pid = Number(projectId)
  const eid = Number(episodeId)
  const navigate = useNavigate()
  const { fitView } = useReactFlow()

  /*
   * episode / fragments / assets 数据
   * linkTargetFragId 正在选资产关联的分镜
   * dirty / busy / error / status 状态
   */
  // 与当前画布列表绑定的版本，独立于运行态刷新。
  const [episode, setEpisode] = useState<DramaEpisode | null>(null)
  const [fragments, setFragmentState] = useState<DramaFragment[]>([])
  const [assets, setAssets] = useState<DramaAsset[]>([])
  const [linkTargetFragId, setLinkTargetFragId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saveState, setSaveState] = useState<FragmentSaveState>({ dirty: false, saving: false, conflict: false })
  const dirty = saveState.dirty
  const fittedRef = useRef(false)
  const fragmentsRef = useRef<DramaFragment[]>([])
  const assetsRef = useRef<DramaAsset[]>([])
  const autosave = useMemo(() => createFragmentAutosave({
    persist: (next, snapshot) => dramaApi.saveFragments(eid, next.map((f, index) => ({
      id: f.id > 0 ? f.id : undefined, sort_order: index,
      content: f.content || '', cover: f.cover || '', video: f.video || '',
      duration_sec: f.duration_sec ?? 8, params: f.params || {}, asset_ids: collectFragmentAssetIds(f),
    })), snapshot),
    onRows: setFragmentState,
    onSaved: setEpisode,
    onState: setSaveState,
    onError: (err) => setError(err instanceof Error ? err.message : translateMessage("workflow.couldNotSave")),
    delay: SAVE_DEBOUNCE_MS,
  }), [eid])
  useEffect(() => { autosave.activate(); return () => autosave.dispose() }, [autosave])
  useFragmentNavigationGuard(autosave)

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<EpisodeFlowNodeData>>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])

  useEffect(() => {
    fragmentsRef.current = fragments
  }, [fragments])

  useEffect(() => {
    assetsRef.current = assets
  }, [assets])

  // 结构键：成片 + 关联资产变化时重建；提示词编辑走局部更新
  const structureKey = fragments
    .map((f) => {
      const aids = collectFragmentAssetIds(f).join(',')
      return `${f.id}:${f.sort_order}:${f.video || ''}:${f.cover || ''}:${f.duration_sec ?? 0}:${aids}`
    })
    .join('|')

  useEffect(() => {
    const flow = buildEpisodeFragmentFlow(fragmentsRef.current, assetsRef.current)
    setNodes(flow.nodes)
    setEdges(flow.edges)
  }, [structureKey, assets, setNodes, setEdges])

  useEffect(() => {
    fittedRef.current = false
  }, [eid])

  useEffect(() => {
    if (fittedRef.current || nodes.length === 0) return
    fittedRef.current = true
    void fitView({ padding: 0.22, duration: 280 })
  }, [nodes.length, fitView])

  // 拉取分集 + 项目资产
  useEffect(() => {
    if (!eid || !pid) return
    let cancelled = false
    setBusy(true)
    setError('')
    Promise.all([dramaApi.getEpisode(eid), dramaApi.listAssets(pid)])
      .then(([ep, assetList]) => {
        if (cancelled) return
        setEpisode(ep)
        autosave.load(ep)
        setAssets(assetList || [])
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : translateMessage("workflow.couldNotLoadTheEpisode"))
      })
      .finally(() => {
        if (!cancelled) setBusy(false)
      })
    return () => {
      cancelled = true
    }
  }, [eid, pid, autosave])

  // 刷新单个分镜节点提示词（关联资产变化靠 structureKey 重建）
  const patchFragmentPrompt = useCallback(
    (fragmentId: number, content: string) => {
      if (readOnlyRef.current) return
      setNodes((prev) =>
        prev.map((node) => {
          if (node.type !== 'episodeFragment') return node
          const data = node.data as EpisodeFragmentNodeData
          if (data.fragmentId !== fragmentId) return node
          return { ...node, data: { ...data, content } }
        }),
      )
    },
    [setNodes],
  )

  // 改提示词
  const handlePromptChange = useCallback(
    (fragmentId: number, content: string) => {
      if (readOnlyRef.current) return
      autosave.edit((prev) =>
        prev.map((f) => {
          if (f.id !== fragmentId) return f
          const updated = { ...f, content }
          return { ...updated, asset_ids: collectFragmentAssetIds(updated) }
        }),
      )
      patchFragmentPrompt(fragmentId, content)
    },
    [patchFragmentPrompt, autosave],
  )

  // 取消关联出境资产
  const handleUnlinkAsset = useCallback(
    (fragmentId: number, assetId: number) => {
      if (readOnlyRef.current) return
      autosave.edit((prev) =>
        prev.map((f) => {
          if (f.id !== fragmentId) return f
          const content = removeAssetMention(f.content || '', assetId)
          const asset_ids = (f.asset_ids || []).filter((id) => id !== assetId)
          return { ...f, content, asset_ids }
        }),
      )
    },
    [autosave],
  )

  // 打开关联选择
  const handleRequestLinkAsset = useCallback((fragmentId: number) => {
      if (readOnlyRef.current) return
    setLinkTargetFragId(fragmentId)
  }, [])

  // 确认关联资产
  const handlePickAsset = useCallback(
    (asset: DramaAsset) => {
      if (readOnlyRef.current) return
      if (linkTargetFragId == null) return
      const fragmentId = linkTargetFragId
      autosave.edit((prev) =>
        prev.map((f) => {
          if (f.id !== fragmentId) return f
          const content = ensureAssetMention(f.content || '', asset.id)
          const asset_ids = Array.from(new Set([...(f.asset_ids || []), asset.id]))
          return { ...f, content, asset_ids }
        }),
      )
      setLinkTargetFragId(null)
    },
    [linkTargetFragId, autosave],
  )

  const nodeTypes: NodeTypes = useMemo(
    () => ({
      episodeFragment: (props) => (
        <EpisodeFragmentNode
          {...props}
          onPromptChange={handlePromptChange}
          onRequestLinkAsset={handleRequestLinkAsset}
        />
      ),
      episodeAsset: (props) => (
        <EpisodeAssetNode {...props} onUnlinkAsset={handleUnlinkAsset} />
      ),
    }),
    [handlePromptChange, handleRequestLinkAsset, handleUnlinkAsset],
  )

  const linkFrag = fragments.find((f) => f.id === linkTargetFragId) || null
  const linkedIdSet = new Set(linkFrag ? collectFragmentAssetIds(linkFrag) : [])
  const pickerAssets = assets.filter((a) => {
    const tab = normalizeAssetTab(a.type || '')
    return Boolean(tab) && !linkedIdSet.has(a.id)
  })

  const backHref = `/drama/projects/${pid}/episodes/${eid}`

  return (
    <div className="ep-storyboard-page">
      <header className="ep-storyboard-topbar">
        <div className="ep-storyboard-topbar-left">
          <button
            type="button"
            className="ep-storyboard-back"
            aria-label={translateUi("workflow.backToEpisode")}
            title={translateUi("workflow.backToEpisode")}
            onClick={() => navigate(backHref)}
          >
            <ChevronLeft size={20} strokeWidth={1.8} />
          </button>
          <div className="ep-storyboard-title">
            <strong>{episode?.name || translateUi("workflow.episode_ebaa6d", { v1: String(eid) })}</strong>
            <span>
              {translateUi("workflow.storyboardShots_0dd76e", { v1: fragments.length, v2: dirty ? translateUi("workflow.unsaved_b01c72") : '' })}</span>
          </div>
        </div>
        <div className="ep-storyboard-actions">
          <button
            type="button"
            className="ep-storyboard-btn ghost"
            onClick={() => navigate(`/drama/projects/${pid}/canvas`)}
          >
            {translateUi("workflow.assetCanvas")}
          </button>
          <button
            type="button"
            className="ep-storyboard-btn dark"
            disabled={readOnly || busy || saveState.saving || !dirty}
            onClick={() => void autosave.flush().catch(() => undefined)}
          >
            {busy || saveState.saving ? translateUi("workflow.saving") : translateUi("workflow.save")}
          </button>
        </div>
        <MyTasksButton />
      </header>

      <div className="ep-storyboard-flow">
        {fragments.length === 0 && !busy ? (
          <div className="ep-storyboard-empty">
            <strong>{translateUi("workflow.noStoryboardShotsYet")}</strong>
            <span>{translateUi("workflow.addShotsInTheEpisodeEditorFirst")}</span>
          </div>
        ) : null}
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          nodeTypes={nodeTypes}
          nodesDraggable={!readOnly}
          nodesConnectable={!readOnly}
          fitView
          minZoom={0.35}
          maxZoom={1.6}
          proOptions={{ hideAttribution: true }}
          defaultEdgeOptions={{ type: 'default' }}
        >
          <Background gap={20} size={1} color="#dbe2ea" />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable />
        </ReactFlow>
        {error ? (
          <p className="ep-storyboard-toast" role="alert">
            {error}
          </p>
        ) : null}
        {busy && fragments.length === 0 ? (
          <p className="ep-storyboard-toast">{translateUi("ecomSet.loading")}</p>
        ) : null}
      </div>

      <Modal
        open={linkTargetFragId != null}
        onClose={() => setLinkTargetFragId(null)}
        title={translateUi("workflow.assetsAppearingInThisShot")}
        size="lg"
      >
        <p className="ep-storyboard-picker-hint">{translateUi("workflow.chooseCharactersScenesAndPropsAppearingInThisShot")}</p>
        {pickerAssets.length === 0 ? (
          <p className="ep-storyboard-picker-empty">{translateUi("workflow.noAssetsAvailableGenerateSomeOnTheAssetCanvas")}</p>
        ) : (
          <div className="ep-storyboard-picker-grid">
            {pickerAssets.map((asset) => {
              const cover = resolveDramaAssetPreviewUrl(asset)
              return (
                <button
                  key={asset.id}
                  type="button"
                  className="ep-storyboard-picker-card"
                  onClick={() => handlePickAsset(asset)}
                >
                  <div className="ep-storyboard-picker-thumb">
                    {cover ? <img src={cover} alt="" /> : <span>{(asset.name || '?')[0]}</span>}
                  </div>
                  <strong>{asset.name || translateUi("workflow.asset", { v1: String(asset.id) })}</strong>
                  <em>{normalizeAssetTab(asset.type || '') || asset.type || translateUi("workflow.asset_713fd9")}</em>
                </button>
              )
            })}
          </div>
        )}
      </Modal>
    </div>
  )
}
