/** 映创官网首页：平台主张 + 样图四宫格 + 平台条 + 产品线卡片 + 三步出片 + 收尾 */
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight, AudioLines, Clapperboard, Film, ImagePlus, Mic, PanelsTopLeft, ShoppingBag, Video, Wand2 } from 'lucide-react'
import AppShell from '../components/layout/AppShell'
import BrandMark from '../components/BrandMark'
import CreateChoiceModal from '../components/ui/CreateChoiceModal'
import { useI18n } from '../i18n'
import { brandFullName } from '../lib/siteLinks'
import { SHORT_VIDEO_CREATE_PATH } from '../lib/shortVideoRoutes'
import './home/home.css'

/* 首屏四宫格样图，顺序与文案 home.showcase 一致 */
const SHOWCASE = [
  '/ecom-examples/scarf_cover.jpg',
  '/char-presets/real.jpg',
  '/image-styles/ancient-romance-soft.jpg',
  '/mode-presets/full.jpg',
]

/* 产品线卡片：入口、图标、样图，顺序与文案 home.lines 一致 */
const LINES = [
  { path: '/ecom', icon: ShoppingBag, image: '/ecom-examples/scarf_street.jpg' },
  { path: '/daihuo', icon: Video, image: '/ecom-examples/scarf_hold.jpg' },
  { path: `${SHORT_VIDEO_CREATE_PATH}?mode=avatar`, icon: Mic, image: '/char-presets/real.jpg' },
  { path: SHORT_VIDEO_CREATE_PATH, icon: Film, image: '/mode-presets/image_text.jpg' },
  { path: '/drama', icon: Clapperboard, image: '/image-styles/chinese-urban-realistic.jpg' },
  { path: '/canvas', icon: PanelsTopLeft, image: '/image-styles/cgi-3d-animation.jpg' },
]

/* 单独使用的生成工具，顺序与文案 home.tools 一致 */
const TOOLS = [
  { path: '/tools/image', icon: ImagePlus },
  { path: '/tools/video', icon: Wand2 },
  { path: '/tools/audio', icon: AudioLines },
]

/** 首页：未登录的创作入口先去登录，登录后回跳到原目标。 */
export default function HomePage() {
  const nav = useNavigate()
  const { t, m, locale } = useI18n()
  const loggedIn = Boolean(localStorage.getItem('token'))
  /* chooserOpen 「开始创作」选择产品线弹层 */
  const [chooserOpen, setChooserOpen] = useState(false)

  // 未登录先去登录，带 next 回跳
  function goAuthOr(path: string) {
    nav(loggedIn ? path : `/auth?next=${encodeURIComponent(path)}`)
  }

  // 开始创作：已登录弹出产品线选择，未登录先注册登录
  function startCreating() {
    if (loggedIn) setChooserOpen(true)
    else nav('/auth?mode=register')
  }

  return (
    <AppShell active="home" flush>
      <section className="kk-hero">
        <div className="kk-hero-copy">
          <p className="kk-eyebrow">
            <i aria-hidden />
            {t('home.eyebrow')}
          </p>
          <h1>
            <span>{t('home.headA')}</span>
            <span className="kk-caption-line">
              {t('home.headB')}
              <i className="kk-cursor" aria-hidden />
            </span>
          </h1>
          <p className="kk-sub">
            {t('home.sub1')}
            <br />
            {t('home.sub2')}
          </p>
          <div className="kk-cta">
            <button type="button" className="kk-pill is-fill" onClick={startCreating}>
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
                {t('home.newHere')} <Link to="/auth?mode=register">{t('home.trial')}</Link>
              </>
            )}
          </p>
        </div>

        <figure className="kk-showcase">
          <div className="kk-showcase-grid">
            {m.home.showcase.map((label, i) => (
              <div key={label} className={`kk-shot is-${i}`}>
                <img src={SHOWCASE[i]} alt="" decoding="async" />
                <span>{label}</span>
              </div>
            ))}
          </div>
          <figcaption className="kk-note">{t('home.showcaseNote')}</figcaption>
        </figure>
      </section>

      <section className="kk-platforms">
        <p>{t('home.platformsLabel')}</p>
        <div>
          {m.home.platforms.map((p) => (
            <span key={p}>{p}</span>
          ))}
        </div>
      </section>

      <section className="kk-lines" id="products">
        <h2>{t('home.linesTitle')}</h2>
        <p className="kk-sub">{t('home.linesSub')}</p>
        <div className="kk-lines-grid">
          {m.home.lines.map((line, i) => {
            const { path, icon: Icon, image } = LINES[i]
            return (
              <button key={line.title} type="button" className="kk-line" onClick={() => goAuthOr(path)}>
                <span className="kk-line-media">
                  <img src={image} alt="" loading="lazy" decoding="async" />
                </span>
                <span className="kk-line-body">
                  <span className="kk-line-head">
                    <Icon size={18} strokeWidth={1.8} aria-hidden />
                    <strong>{line.title}</strong>
                    <ArrowRight className="kk-line-go" size={16} strokeWidth={2} aria-hidden />
                  </span>
                  <span className="kk-line-desc">{line.desc}</span>
                  <span className="kk-line-tags">
                    {line.tags.map((tag) => (
                      <em key={tag}>{tag}</em>
                    ))}
                  </span>
                </span>
              </button>
            )
          })}
        </div>
        <div className="kk-tools">
          <span>{t('home.toolsLabel')}</span>
          {m.home.tools.map((label, i) => {
            const { path, icon: Icon } = TOOLS[i]
            return (
              <button key={label} type="button" onClick={() => goAuthOr(path)}>
                <Icon size={16} strokeWidth={1.8} aria-hidden />
                {label}
              </button>
            )
          })}
        </div>
      </section>

      <section className="kk-steps">
        <h2>{t('home.stepsTitle')}</h2>
        <ol>
          {m.home.steps.map((step, i) => (
            <li key={step.title}>
              <span className="kk-step-no">0{i + 1}</span>
              <strong>{step.title}</strong>
              <p>{step.desc}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="kk-close">
        <h2>
          <span className="kk-caption-line">{t('home.closeTitle')}</span>
        </h2>
        <p className="kk-sub">{t('home.closeSub')}</p>
        <button type="button" className="kk-pill is-fill" onClick={startCreating}>
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
          © {new Date().getFullYear()} {brandFullName(locale)}
        </p>
      </footer>

      <CreateChoiceModal open={chooserOpen} onClose={() => setChooserOpen(false)} />
    </AppShell>
  )
}
