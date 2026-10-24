import { useEffect, useState } from 'react'
import type { Page } from './types'

/** 有防抖与过期响应保护的服务端分页查询；key 必须包含筛选和团队。 */
export function useTeamPage<T>(key: string, load: (signal: AbortSignal) => Promise<Page<T>>, revision = 0) {
  /* data 页数据；loading/error 加载反馈；retry 显式重试。 */
  const [data, setData] = useState<Page<T>>({ items: [], meta: { page: 1, page_size: 12, total: 0 } })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    const timer = window.setTimeout(() => {
      void load(controller.signal).then((result) => { if (!controller.signal.aborted) setData(result) }).catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '加载失败，请重试') }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    }, 180)
    return () => { window.clearTimeout(timer); controller.abort() }
    // load 由 key 描述，避免组件内联函数导致无穷重载。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, revision, retry])
  return { ...data, loading, error, retry: () => setRetry((n) => n + 1) }
}
