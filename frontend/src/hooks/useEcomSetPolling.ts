import { useEffect, useRef } from 'react'
import { getEcomSet, type EcomSet } from '../api/ecomSet'
import { ECOM_POLL_MS, needsPolling } from '../lib/ecomSet'

/**
 * 套图生成中每 3 秒轮询详情；进入终态、切换套图或组件卸载时清除定时器。
 * @param set 当前套图
 * @param onUpdate 拉到新数据时回调
 */
export function useEcomSetPolling(set: EcomSet | null, onUpdate: (next: EcomSet) => void) {
  const active = needsPolling(set)
  const id = set?.id ?? null
  const onUpdateRef = useRef(onUpdate)

  useEffect(() => {
    onUpdateRef.current = onUpdate
  }, [onUpdate])

  useEffect(() => {
    if (!active || id == null) return
    let cancelled = false
    let timer = 0
    // 串行轮询：上一次返回后再排下一次，避免慢请求叠加
    const schedule = () => {
      timer = window.setTimeout(async () => {
        try {
          const next = await getEcomSet(id)
          if (cancelled) return
          onUpdateRef.current(next)
          if (!needsPolling(next)) return
        } catch {
          /* 网络抖动忽略，下一轮重试 */
        }
        if (!cancelled) schedule()
      }, ECOM_POLL_MS)
    }
    schedule()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [active, id])
}
