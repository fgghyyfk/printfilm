import { useI18n } from '../../../i18n'
/** 画布左侧垂直居中工具栏与添加节点面板 */
import { useEffect, useRef, useState } from 'react'
import { FolderOpen, Plus, X } from 'lucide-react'
import { useCanvasStore } from './CanvasStore'
import { ADD_NODE_OPTIONS, CANVAS_NODE_OPTION_BY_KIND, type CanvasNodeKind } from './canvasTypes'

type CanvasLeftToolbarProps = {
  onSelectNode: (kind: CanvasNodeKind) => void
}

/** 渲染画布左侧浮动工具栏 */
export function CanvasLeftToolbar({ onSelectNode }: CanvasLeftToolbarProps) {
  const { t: translateUi } = useI18n()
  const { nodes, requestFocusNode } = useCanvasStore()
  /*
   * panelOpen 添加节点面板
   * folderOpen 节点列表面板
   */
  const [panelOpen, setPanelOpen] = useState(false)
  const [folderOpen, setFolderOpen] = useState(false)
  const addAnchorRef = useRef<HTMLDivElement>(null)

  // 点击外侧关闭添加面板
  useEffect(() => {
    if (!panelOpen) return
    const onPointerDown = (event: PointerEvent) => {
      const root = addAnchorRef.current
      if (!root) return
      if (event.target instanceof Node && root.contains(event.target)) return
      setPanelOpen(false)
    }
    window.addEventListener('pointerdown', onPointerDown)
    return () => window.removeEventListener('pointerdown', onPointerDown)
  }, [panelOpen])

  return (
    <div className="fc-overlay fc-left-toolbar">
      <div className="fc-left-stack">
        <div
          ref={addAnchorRef}
          className={`fc-add-anchor${panelOpen ? ' is-open' : ''}`}
          onMouseEnter={() => setPanelOpen(true)}
          onMouseLeave={() => setPanelOpen(false)}
        >
          <button
            type="button"
            className={`fc-icon-btn is-primary${panelOpen ? ' is-open' : ''}`}
            aria-label={panelOpen ? translateUi("workflow.closeAddNodeMenu") : translateUi("workflow.addNode")}
            aria-expanded={panelOpen}
            title={translateUi("workflow.addNode")}
            onClick={(event) => {
              event.stopPropagation()
              setPanelOpen((v) => !v)
              setFolderOpen(false)
            }}
          >
            {panelOpen ? <X size={18} strokeWidth={2} /> : <Plus size={18} strokeWidth={2} />}
          </button>

          {panelOpen ? (
            <div className="fc-add-panel-bridge" role="menu" aria-label={translateUi("workflow.nodeTypes")}>
              <div className="fc-add-panel">
                {ADD_NODE_OPTIONS.map((option) => {
                  const Icon = option.icon
                  return (
                    <button
                      key={option.id}
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        onSelectNode(option.id)
                        setPanelOpen(false)
                      }}
                    >
                      <span className="fc-add-panel-icon">
                        <Icon size={14} strokeWidth={1.8} />
                      </span>
                      {option.label}
                    </button>
                  )
                })}
              </div>
            </div>
          ) : null}
        </div>

        <button
          type="button"
          className={`fc-icon-btn${folderOpen ? ' is-active' : ''}`}
          aria-label={translateUi("workflow.assetFolders")}
          title={translateUi("workflow.assetFolders")}
          aria-expanded={folderOpen}
          onClick={() => {
            setFolderOpen((v) => !v)
            setPanelOpen(false)
          }}
        >
          <FolderOpen size={18} strokeWidth={1.8} />
        </button>

        {folderOpen ? (
          <div className="fc-folder-panel" role="dialog" aria-label={translateUi("workflow.canvasNodeList")}>
            <h4>{translateUi("workflow.canvasNodes")}</h4>
            {nodes.length === 0 ? (
              <p className="fc-folder-empty">{translateUi("workflow.noNodesYetClickToAddOne")}</p>
            ) : (
              nodes.map((node) => {
                const option = CANVAS_NODE_OPTION_BY_KIND[node.data.kind]
                const Icon = option.icon
                return (
                  <button
                    key={node.id}
                    type="button"
                    className="fc-folder-item"
                    onClick={() => {
                      requestFocusNode(node.id)
                      setFolderOpen(false)
                    }}
                  >
                    <Icon size={14} strokeWidth={1.8} />
                    <span>
                      {option.label} · {node.data.label}
                    </span>
                  </button>
                )
              })
            )}
          </div>
        ) : null}
      </div>
    </div>
  )
}
