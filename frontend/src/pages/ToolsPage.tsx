import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import AppShell from '../components/layout/AppShell'
import ComingSoon from '../components/ui/ComingSoon'
import { useI18n } from '../i18n'
import { PRODUCT_ICONS, localizeToolDefs, toolHref } from '../lib/toolsCatalog'

// 电商工具归入同组，其他创作工具保留原有卡片。
export default function ToolsPage() {
  const { t, m } = useI18n()
  const DramaIcon = PRODUCT_ICONS.drama
  const KepuIcon = PRODUCT_ICONS.kepu
  const tools = localizeToolDefs(m)

  return (
    <AppShell active="tools">
      <div className="pf-page-head">
        <div>
          <h1>{t('tools.title')}</h1>
          <p className="pf-muted">{t('tools.lead')}</p>
        </div>
      </div>
      {['ecom', 'creative'].map((group) => <section key={group} className="pf-job-section">
      <h2>{t(group === 'ecom' ? 'ecomHub.title' : 'tools.title')}</h2>
      <div className="pf-tools-grid pf-tools-grid-lg">
        {tools.filter((tool) => (tool.group === 'ecom') === (group === 'ecom')).map((tool) => {
          const Icon = tool.icon
          return (
            <Link
              key={tool.id}
              to={toolHref(tool)}
              className={`pf-tools-card pf-tools-card-lg${tool.soon ? ' is-soon' : ''}`}
            >
              <div className="pf-tools-card-top">
                <span className="pf-ws-tool-icon" aria-hidden>
                  <Icon size={22} strokeWidth={1.6} />
                </span>
                <span className="pf-tools-arrow" aria-hidden>
                  <ArrowRight size={14} strokeWidth={2} />
                </span>
              </div>
              <h3>
                {tool.title}
                {tool.soon ? <ComingSoon /> : null}
              </h3>
              <p>{tool.desc}</p>
            </Link>
          )
        })}
      </div>
      </section>)}
      <section className="pf-ws-products" style={{ marginTop: '2.5rem' }}>
        <Link to="/drama" className="pf-ws-product pf-ws-product-drama">
          <span className="pf-ws-product-icon" aria-hidden>
            <DramaIcon size={26} strokeWidth={1.6} />
          </span>
          <span className="pf-ws-product-body">
            <strong>{t('tools.goDrama')}</strong>
            <span>{t('tools.goDramaHint')}</span>
          </span>
          <span className="pf-ws-product-go" aria-hidden>
            <ArrowRight size={16} strokeWidth={2} />
          </span>
        </Link>
        <Link to="/history" className="pf-ws-product pf-ws-product-kepu">
          <span className="pf-ws-product-icon" aria-hidden>
            <KepuIcon size={26} strokeWidth={1.6} />
          </span>
          <span className="pf-ws-product-body">
            <strong>{t('tools.goKepu')}</strong>
            <span>{t('tools.goKepuHint')}</span>
          </span>
          <span className="pf-ws-product-go" aria-hidden>
            <ArrowRight size={16} strokeWidth={2} />
          </span>
        </Link>
      </section>
    </AppShell>
  )
}
