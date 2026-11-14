import { useEffect, useState } from 'react'
import { estimateStudioTool, type StudioToolEstimate, type StudioToolEstimatePayload } from '../api/tools'
import { isRequestAborted } from '../api/http'

/** 对当前视频参数取得服务端报价，旧参数的迟到结果不能用于提交。 */
export function useGenerationEstimate(payload: StudioToolEstimatePayload | null) {
  /* key 当前参数快照；state 报价及其所属参数；revision 显式重试或价格更新。 */
  const key = payload ? JSON.stringify(payload) : ''
  const [state, setState] = useState<{ key: string; quote?: StudioToolEstimate; error?: string } | null>(null)
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    if (!key) return
    let disposed = false
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      void estimateStudioTool(JSON.parse(key) as StudioToolEstimatePayload, controller.signal).then(
        (quote) => { if (!disposed) setState({ key, quote }) },
        (cause) => { if (!disposed && !isRequestAborted(cause)) setState({ key, error: cause instanceof Error ? cause.message : '预计消耗暂不可用，请重试' }) },
      )
    }, 300)
    return () => { disposed = true; clearTimeout(timer); controller.abort() }
  }, [key, revision])

  useEffect(() => {
    /** 管理端更新价格或重新聚焦时重新报价，不自动重发生成请求。 */
    function refresh() { setState(null); setRevision(value => value + 1) }
    // 报价接口自己的 409 也会广播；已有有效报价才响应，避免缺价时循环请求。
    function refreshPrices() { if (state?.quote) refresh() }
    window.addEventListener('media-prices-changed', refreshPrices)
    window.addEventListener('focus', refresh)
    return () => { window.removeEventListener('media-prices-changed', refreshPrices); window.removeEventListener('focus', refresh) }
  }, [state?.quote])

  /** 报价失败后由用户重试。 */
  function retry() { setState(null); setRevision(value => value + 1) }
  const current = state?.key === key ? state : null
  return { quote: current?.quote, error: current?.error, loading: Boolean(key && !current), retry }
}
