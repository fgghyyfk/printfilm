import { useEffect, useState } from 'react'
import type { ToolRunRecord } from '../api/tools'
import { getGenerationTaskDetail } from '../api/generationTasks'
import { toolRunDetailAsset } from '../lib/toolRunDetails'
import { useGenerationText } from '../lib/generationMedia'
import { subscribeWorkspace, workspaceScope } from '../features/teams/workspaceScope'

/** 管理选中生成记录及刷新；展示、复制和下载统一交给全局资产详情。 */
export function useToolRunDetail() {
  const text = useGenerationText()
  /* detail 选中任务的最新快照；error 更新错误；resultIndex 当前预览结果。 */
  const [detail, setDetail] = useState<ToolRunRecord | null>(null)
  const [error, setError] = useState('')
  const [resultIndex, selectResult] = useState(0)
  const recordId = detail?.id
  const recordSource = detail?.source || 'tool'

  useEffect(() => subscribeWorkspace(() => { setDetail(null); setError(''); selectResult(0) }), [])

  useEffect(() => {
    if (recordId === undefined) return
    // 先展示点击时的快照，异步结果只允许回填本次选中的任务。
    const selectedRecord = { id: recordId, source: recordSource }
    const epoch = workspaceScope.epoch
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    /** 任务移出筛选列表后继续更新详情，终态停止轮询。 */
    async function refresh() {
      try {
        const record = await getGenerationTaskDetail(selectedRecord)
        if (disposed || epoch !== workspaceScope.epoch) return
        setDetail(record)
        setError('')
        if (record.status === 'queued' || record.status === 'running') timer = setTimeout(refresh, 5000)
      } catch (cause) {
        if (disposed || epoch !== workspaceScope.epoch) return
        setError(cause instanceof Error ? cause.message : text('详情更新失败，正在重试', 'Could not update details. Retrying.'))
        timer = setTimeout(refresh, 10000)
      }
    }
    void refresh()
    return () => { disposed = true; clearTimeout(timer) }
  }, [recordId, recordSource, text])

  /** 每次打开都使用新快照并重置结果序号与上次错误。 */
  function openDetail(record: ToolRunRecord) {
    setDetail(record)
    selectResult(0)
    setError('')
  }

  /** 关闭后卸载全局弹窗，同时停止详情轮询。 */
  function closeDetail() { setDetail(null) }

  return { detail, asset: detail ? toolRunDetailAsset(detail, resultIndex) : null, error, resultIndex, selectResult, openDetail, closeDetail }
}
