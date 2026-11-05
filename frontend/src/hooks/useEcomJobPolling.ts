import { useEffect, useRef } from 'react'
import { getEcomJob, type EcomJob } from '../api/ecomJobs'
import { ecomJobNeedsPolling } from '../lib/ecomJobs'

/** 串行轮询批量任务，终态、切换或卸载后取消定时；网络错误交给页面展示。 */
export function useEcomJobPolling(job: EcomJob | null, onUpdate: (job: EcomJob) => void, onError: (error: unknown) => void) {
  /* 回调引用保持最新，避免每次任务更新重建计时器。 */
  const updateRef = useRef(onUpdate)
  const errorRef = useRef(onError)
  const id = job?.id
  const active = ecomJobNeedsPolling(job)
  useEffect(() => { updateRef.current = onUpdate; errorRef.current = onError }, [onUpdate, onError])
  useEffect(() => {
    if (!active || !id) return
    let cancelled = false
    let timer = 0
    // 上次请求完成后再安排下一次，避免慢请求堆积。
    function schedule() {
      timer = window.setTimeout(async () => {
        try {
          const next = await getEcomJob(id!)
          if (cancelled) return
          updateRef.current(next)
          if (!ecomJobNeedsPolling(next)) return
        } catch (error) {
          if (!cancelled) errorRef.current(error)
        }
        if (!cancelled) schedule()
      }, 3000)
    }
    schedule()
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [active, id])
}
