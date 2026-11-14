import { translate as translateUi } from '../../../i18n/runtime'
/** 画布布局脏标记 → 防抖自动保存 */
import { useCallback, useEffect, useRef } from 'react'
import type { Edge, Node } from '@xyflow/react'
import { dramaApi } from '../../../api/drama'
import { CANVAS_AUTO_SAVE_MS, type CanvasAssetNodeData } from './canvasTypes'

export type CanvasSaveStatus = 'idle' | 'dirty' | 'saving' | 'saved' | 'error'

type UseCanvasAutoSaveArgs = {
  projectId: number
  nodes: Node<CanvasAssetNodeData>[]
  edges: Edge[]
  dirty: boolean
  enabled: boolean
  onSaved: () => void
  onError: (message: string) => void
  onStatus?: (status: CanvasSaveStatus) => void
}

/** 脏数据防抖保存到 drama canvas API；保存中再改会继续刷到最新快照 */
export function useCanvasAutoSave({
  projectId,
  nodes,
  edges,
  dirty,
  enabled,
  onSaved,
  onError,
  onStatus,
}: UseCanvasAutoSaveArgs) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const savingRef = useRef(false)
  const queuedRef = useRef(false)
  const dirtyEpochRef = useRef(0)
  const latestRef = useRef({ nodes, edges, projectId })
  latestRef.current = { nodes, edges, projectId }
  // pendingRef 是否有尚未落盘的改动；防抖定时器在卸载时会被清掉，靠它补存
  const pendingRef = useRef(false)
  pendingRef.current = enabled && dirty

  /** 立刻保存；可传入覆盖节点/边（删除后未等一轮渲染时用） */
  const flush = useCallback(
    async (override?: {
      nodes: Node<CanvasAssetNodeData>[]
      edges: Edge[]
    }) => {
      if (savingRef.current) {
        queuedRef.current = true
        return
      }
      savingRef.current = true
      onStatus?.('saving')
      let oneShot = override
      try {
        while (true) {
          queuedRef.current = false
          const epoch = dirtyEpochRef.current
          const base = latestRef.current
          const payloadNodes = oneShot?.nodes ?? base.nodes
          const payloadEdges = oneShot?.edges ?? base.edges
          oneShot = undefined
          await dramaApi.saveCanvas({
            project_id: base.projectId,
            nodes: payloadNodes.map((n) => ({
              id: n.id,
              type: n.type,
              position: n.position,
              data: n.data,
            })),
            edges: payloadEdges.map((e) => ({
              id: e.id,
              source: e.source,
              target: e.target,
            })),
          })
          if (queuedRef.current || dirtyEpochRef.current !== epoch) {
            continue
          }
          onSaved()
          onStatus?.('saved')
          return
        }
      } catch (err) {
        onStatus?.('error')
        onError(err instanceof Error ? err.message : translateUi("workflow.autosaveFailed"))
        // 网络闪断时保持 dirty，便于稍后自动重试 / Ctrl+S
        throw err
      } finally {
        savingRef.current = false
        if (queuedRef.current) {
          queuedRef.current = false
          void flush().catch(() => undefined)
        }
      }
    },
    [onError, onSaved, onStatus],
  )

  useEffect(() => {
    if (!enabled || !dirty) return

    dirtyEpochRef.current += 1
    onStatus?.('dirty')

    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => {
      void flush().catch(() => undefined)
    }, CANVAS_AUTO_SAVE_MS)

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [dirty, enabled, nodes, edges, flush, onStatus])

  // 离开画布时立刻保存还在防抖中的改动，不能丢；只在卸载时执行。
  const flushRef = useRef(flush)
  flushRef.current = flush
  useEffect(() => {
    return () => {
      if (pendingRef.current) void flushRef.current().catch(() => undefined)
    }
  }, [])

  return { flush }
}
