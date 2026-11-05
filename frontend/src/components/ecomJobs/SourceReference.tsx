import { resolveToolMediaUrl } from '../../api/tools'
import { useI18n } from '../../i18n'
import type { useRefineSource } from '../ecomSet/useRefineSource'
import Button from '../ui/Button'

type Props = { value: ReturnType<typeof useRefineSource>; kind: 'style' | 'sku' }

/** 显示跨工具带来的来源、失败重试和重新上传入口。 */
export default function SourceReference({ value, kind }: Props) {
  const { t } = useI18n()
  if (!value.source && !value.loading && !value.error) return null
  return <div className="pf-job-source">
    {value.source ? <><img src={resolveToolMediaUrl(value.source.url)} alt={t('ecomHub.sourceReady')} /><p>{t(kind === 'style' ? 'ecomHub.sourceStyle' : 'ecomHub.sourceSku')}</p></> : null}
    {value.loading ? <p role="status">{t('ecomHub.sourceLoading')}</p> : null}
    {value.error ? <div role="alert"><p className="pf-tool-error">{value.error}</p><Button type="button" variant="outline" onClick={value.retry}>{t('ecomJobs.retry')}</Button></div> : null}
    <Button variant="text" to={`/ecom/${kind}`}>{t('ecomHub.changeSource')}</Button>
  </div>
}
