import { Link, Navigate, useLocation } from 'react-router-dom'
import { ArrowRight, ShoppingBag } from 'lucide-react'
import AppShell from '../../components/layout/AppShell'
import RecentTasks from '../../components/ecomJobs/RecentTasks'
import { useI18n } from '../../i18n'
import { ECOM_GROUPS, ECOM_PATHS } from '../../lib/ecomHub'

/** 电商工作台登录守卫，保留任务入口回跳地址。 */
export default function EcomHubPage() {
  const location = useLocation()
  if (!localStorage.getItem('token')) return <Navigate to={`/auth?next=${encodeURIComponent('/ecom' + location.search)}`} replace />
  return <EcomHubWorkspace />
}

/** 按素材、出图、出海、视频展示工具，下方复用服务端聚合任务历史。 */
function EcomHubWorkspace() {
  const { t } = useI18n()
  return <AppShell active="ecom"><div className="pf-tool-detail pf-job-page">
    <header className="pf-tool-detail-head"><ShoppingBag size={26} aria-hidden /><div><h1>{t('ecomHub.title')}</h1><p className="pf-muted">{t('ecomHub.lead')}</p></div></header>
    {ECOM_GROUPS.map((group) => <section className="pf-job-section" key={group.key}>
      <h2>{t(`ecomHub.${group.key}`)}</h2><div className="pf-tools-grid">
        {group.tools.map((kind) => <Link className="pf-tools-card" key={kind} to={ECOM_PATHS[kind]}>
          <h3>{t(`ecomHub.kinds.${kind}`)} <ArrowRight size={14} aria-hidden /></h3><p>{t(`ecomHub.descriptions.${kind}`)}</p>
        </Link>)}
      </div>
    </section>)}
    <section className="pf-job-section"><h2>{t('ecomHub.video')}</h2><p>{t('ecomHub.videoHint')}</p><p className="pf-muted">{t('ecomHub.videoChoose')}</p><Link className="pf-btn pf-btn-outline" to="/daihuo">{t('ecomHub.videoStart')}</Link></section>
    <RecentTasks />
  </div></AppShell>
}
