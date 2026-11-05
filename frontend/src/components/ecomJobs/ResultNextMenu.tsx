import { Link } from 'react-router-dom'
import { useI18n } from '../../i18n'
import { nextStepHref } from '../../lib/ecomHub'

/** 所有批量结果共用的下一步菜单，只传任务与图片标识。 */
export default function ResultNextMenu({ jobId, idx }: { jobId: number; idx: number }) {
  const { t } = useI18n()
  return <details className="pf-ecom-next-menu">
    <summary>{t('ecomHub.next')}</summary>
    <nav aria-label={t('ecomHub.next')}>
      <Link to={nextStepHref('set', jobId, idx)}>{t('ecomHub.useSet')}</Link>
      <Link to={nextStepHref('style', jobId, idx)}>{t('ecomHub.useStyle')}</Link>
      <Link to={nextStepHref('sku', jobId, idx)}>{t('ecomHub.useSku')}</Link>
    </nav>
  </details>
}
