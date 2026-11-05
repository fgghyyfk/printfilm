import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getEcomJob } from '../../api/ecomJobs'
import { useI18n } from '../../i18n'
import { parseRefineSource } from '../../lib/ecomSet'

/** 从 URL 读取精修来源并加载预览；创建套图时由后端再次校验归属和状态。 */
export function useRefineSource() {
  const [params] = useSearchParams()
  const { t } = useI18n()
  const job = params.get('source_job_id'), item = params.get('source_item_idx')
  const source = parseRefineSource(job, item)
  /* value 来源预览；error 加载错误；loading 加载状态；retryKey 手动重试。 */
  const [value, setValue] = useState<{ jobId: number; idx: number; url: string } | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [retryKey, setRetryKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    setValue(null)
    setError('')
    setLoading(false)
    const parsed = parseRefineSource(job, item)
    if (!parsed) {
      if (job !== null || item !== null) setError(t('ecomSet.enhanced.sourceError'))
      return
    }
    setLoading(true)
    getEcomJob(parsed.jobId).then((result) => {
      if (cancelled) return
      const image = result.items.find((image) => image.idx === parsed.idx)
      if (result.kind !== 'refine' || image?.status !== 'succeeded' || !image.result_url) {
        setError(t('ecomSet.enhanced.sourceError'))
      } else {
        setValue({ ...parsed, url: image.result_url })
      }
    }).catch(() => {
      if (!cancelled) setError(t('ecomSet.enhanced.sourceError'))
    }).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [job, item, retryKey, t])

  // 只向当前 URL 暴露匹配的预览，避免 URL 切换时短暂使用旧图。
  const current = value && source?.jobId === value.jobId && source.idx === value.idx ? value : null
  return { source: current, error, loading, retry: () => setRetryKey((key) => key + 1) }
}
