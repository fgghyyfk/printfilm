/** 开口播官网首页：简洁风，居中主张 + 产品示意窗 + 平台条 + 流程标签页 + 其他出片方式 */
import { useState, type KeyboardEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, Clapperboard, ShoppingBag, Video, Wand2 } from 'lucide-react'
import AppShell from '../components/layout/AppShell'
import BrandMark from '../components/BrandMark'
import HomeFlowPreview from './home/HomeFlowPreview'
import { useI18n } from '../i18n'
import { BRAND_NAME, BRAND_NAME_EN } from '../lib/siteLinks'
import './home/home.css'

const AVATAR_PATH = '/studio/new?mode=avatar'
const MORE_LINKS = [
  { path: '/daihuo', icon: ShoppingBag },
  { path: '/studio/new', icon: Video },
  { path: '/drama', icon: Clapperboard },
  { path: '/tools/image', icon: Wand2 },
]

/** 首页：未登录的创作入口先去登录，登录后回跳到原目标。 */
export default function HomePage() {
  const nav = useNavigate()
  const { t, m } = useI18n()
  const loggedIn = Boolean(localStorage.getItem('token'))
  /* flowIdx 流程标签页当前项 */
  const [flowIdx, setFlowIdx] = useState(0)
  const flow = m.home.flow[flowIdx]

  // 未登录先去登录，带 next 回跳
  function goAuthOr(path: string) {
    nav(loggedIn ? path : `/auth?next=${encodeURIComponent(path)}`)
  }

  // 流程标签页的方向键切换（WAI-ARIA tabs 约定），焦点跟随选中项
  function onFlowKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const count = m.home.flow.length
    const target = { ArrowRight: flowIdx + 1, ArrowLeft: flowIdx - 1, Home: 0, End: count - 1 }[e.key]
    if (target === undefined) return
    e.preventDefault()
    const next = (target + count) % count
    setFlowIdx(next)
    e.currentTarget.querySelectorAll('button')[next]?.focus()
  }

  return (
    <AppShell active="home" flush>
      <section className="kk-hero">
        <p className="kk-eyebrow">{t('home.eyebrow')}</p>
        <h1>
          <span className="kk-brand">
            {BRAND_NAME}
            <i className="kk-cursor" aria-hidden />
          </span>{' '}
          {t('home.headline')}
        </h1>
        <p className="kk-sub">
          {t('home.sub1')}
          <br />
          {t('home.sub2')}
        </p>
        <div className="kk-cta">
          <button type="button" className="kk-pill is-fill" onClick={() => goAuthOr(AVATAR_PATH)}>
            {t('home.start')}
          </button>
          <button type="button" className="kk-pill is-line" onClick={() => goAuthOr('/ecom')}>
            {t('home.ecom')}
          </button>
        </div>
        <p className="kk-cta-note">
          {loggedIn ? (
            <Link to="/tasks">{t('home.myTasks')}</Link>
          ) : (
            <>
              {t('home.newHere')}{' '}
              <Link to={`/auth?mode=register&next=${encodeURIComponent(AVATAR_PATH)}`}>{t('home.trial')}</Link>
            </>
          )}
        </p>

        <div className="kk-window" aria-hidden>
          <div className="kk-window-bar">
            <span className="kk-lights">
              <i />
              <i />
              <i />
            </span>
            <span className="kk-window-title">{t('home.winTitle')}</span>
            <span className="kk-window-status">{t('home.winStatus')}</span>
          </div>
          <div className="kk-window-body">
            <aside className="kk-window-side">
              {m.home.winSteps.map((s, i) => (
                <span key={s} className={i === 3 ? 'is-on' : i < 3 ? 'is-done' : ''}>
                  <b />
                  {s}
                </span>
              ))}
            </aside>
            <div className="kk-window-main">
              <p className="kk-window-task">{t('home.winTask')}</p>
              <div className="kk-window-grid">
                <div className="kk-phone">
                  <img src="/char-presets/real.jpg" alt="" width={170} height={302} decoding="async" />
                  <div className="kk-phone-title">
                    <span className="is-top">{t('home.winTitleTop')}</span>
                    <span>{t('home.winTitleBottom')}</span>
                  </div>
                </div>
                <ul className="kk-rows">
                  {m.home.winRows.map((r, i) => (
                    <li key={r.label} className={`is-${i < 2 ? 'done' : i === 2 ? 'run' : 'wait'}`}>
                      <span>{r.label}</span>
                      <span className="kk-bar">
                        <i />
                      </span>
                      <em>{r.state}</em>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </div>
        <p className="kk-note">{t('home.winNote')}</p>
      </section>

      <section className="kk-platforms">
        <p>{t('home.platformsLabel')}</p>
        <div>
          {m.home.platforms.map((p) => (
            <span key={p}>{p}</span>
          ))}
        </div>
      </section>

      <section className="kk-flow" id="how">
        <h2>
          <span className="kk-brand-sm">{BRAND_NAME}.</span> {t('home.flowTitle')}
        </h2>
        <p className="kk-sub">{t('home.flowSub')}</p>
        <div className="kk-tabs" role="tablist" aria-label={t('home.flowTitle')} onKeyDown={onFlowKeyDown}>
          {m.home.flow.map((f, i) => (
            <button
              key={f.tab}
              type="button"
              role="tab"
              id={`kk-flow-tab-${i}`}
              aria-selected={i === flowIdx}
              aria-controls="kk-flow-panel"
              tabIndex={i === flowIdx ? 0 : -1}
              className={i === flowIdx ? 'is-on' : ''}
              onClick={() => setFlowIdx(i)}
            >
              {f.tab}
            </button>
          ))}
        </div>
        <div className="kk-flow-panel" id="kk-flow-panel" role="tabpanel" aria-labelledby={`kk-flow-tab-${flowIdx}`}>
          <div key={flowIdx} className="kk-flow-copy">
            <span className="kk-flow-no">0{flowIdx + 1}</span>
            <h3>{flow.title}</h3>
            <p>{flow.desc}</p>
          </div>
          <HomeFlowPreview key={`preview-${flowIdx}`} step={flowIdx} />
        </div>
      </section>

      <section className="kk-more">
        <h2>{t('home.moreTitle')}</h2>
        <div className="kk-more-grid">
          {m.home.more.map((item, i) => {
            const { path, icon: Icon } = MORE_LINKS[i]
            return (
              <button key={item.title} type="button" className="kk-more-card" onClick={() => goAuthOr(path)}>
                <Icon size={20} strokeWidth={1.7} aria-hidden />
                <strong>{item.title}</strong>
                <span>{item.desc}</span>
                <ArrowRight className="kk-more-go" size={16} strokeWidth={2} aria-hidden />
              </button>
            )
          })}
        </div>
      </section>

      <section className="kk-close">
        <h2>{t('home.closeTitle')}</h2>
        <p className="kk-sub">{t('home.closeSub')}</p>
        <button type="button" className="kk-pill is-fill" onClick={() => goAuthOr(AVATAR_PATH)}>
          {t('home.start')}
        </button>
      </section>

      <footer className="kk-foot">
        <div className="kk-foot-brand">
          <BrandMark />
          <span>{t('home.footBrand')}</span>
        </div>
        <nav aria-label={t('home.footNav')}>
          <Link to="/pricing">{t('nav.pricing')}</Link>
          <Link to="/help">{t('footer.help')}</Link>
          <Link to="/contact">{t('footer.contact')}</Link>
          <Link to="/terms">{t('footer.terms')}</Link>
          <Link to="/privacy">{t('footer.privacy')}</Link>
        </nav>
        <p>
          © {new Date().getFullYear()} {BRAND_NAME} {BRAND_NAME_EN}
        </p>
      </footer>
    </AppShell>
  )
}
