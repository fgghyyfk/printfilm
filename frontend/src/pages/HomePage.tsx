/** 映创官网首页（深色科技风）：主张 + 生成控制台样图 + 平台条 + 产品线卡片 + 三步出片 + 收尾 */
import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  ArrowRight,
  AudioLines,
  Clapperboard,
  Film,
  ImagePlus,
  Mic,
  PanelsTopLeft,
  ShoppingBag,
  Sparkles,
  Video,
  Wand2,
} from 'lucide-react'
import AppShell from '../components/layout/AppShell'
import BrandMark from '../components/BrandMark'
import CreateChoiceModal from '../components/ui/CreateChoiceModal'
import { useI18n } from '../i18n'
import { brandFullName } from '../lib/siteLinks'
import { SHORT_VIDEO_CREATE_PATH } from '../lib/shortVideoRoutes'
import './home/home.css'

/* 首屏控制台四宫格样图（统一风格专门生成，见 public/home/），顺序与文案 home.showcase 一致 */
const SHOWCASE = [
  '/home/showcase-ecom.jpg',
  '/home/showcase-avatar.jpg',
  '/home/showcase-drama.jpg',
  '/home/showcase-video.jpg',
]

/* 产品线卡片：入口、图标、样图，顺序与文案 home.lines 一致 */
const LINES = [
  { path: '/ecom', icon: ShoppingBag, image: '/home/line-ecom.jpg' },
  { path: '/daihuo', icon: Video, image: '/home/line-daihuo.jpg' },
  { path: `${SHORT_VIDEO_CREATE_PATH}?mode=avatar`, icon: Mic, image: '/home/line-avatar.jpg' },
  { path: SHORT_VIDEO_CREATE_PATH, icon: Film, image: '/home/line-video.jpg' },
  { path: '/drama', icon: Clapperboard, image: '/home/line-drama.jpg' },
  { path: '/canvas', icon: PanelsTopLeft, image: '/home/line-canvas.jpg' },
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
      <div className="yc-home">
        <div className="yc-bg" aria-hidden>
          <i className="yc-glow is-a" />
          <i className="yc-glow is-b" />
        </div>

        <section className="yc-hero">
          <div className="yc-hero-copy">
            <p className="yc-eyebrow">
              <i aria-hidden />
              {t('home.eyebrow')}
            </p>
            <h1>
              <span>{t('home.headA')}</span>
              <span className="yc-grad">{t('home.headB')}</span>
            </h1>
            <p className="yc-sub">
              {t('home.sub1')}
              <br />
              {t('home.sub2')}
            </p>
            <div className="yc-cta">
              <button type="button" className="yc-btn is-primary" onClick={startCreating}>
                {t('home.start')}
                <ArrowRight size={18} strokeWidth={2.2} aria-hidden />
              </button>
              <button type="button" className="yc-btn is-ghost" onClick={() => goAuthOr('/ecom')}>
                {t('home.ecom')}
              </button>
            </div>
            <p className="yc-cta-note">
              {loggedIn ? (
                <Link to="/tasks">{t('home.myTasks')}</Link>
              ) : (
                <>
                  {t('home.newHere')} <Link to="/auth?mode=register">{t('home.trial')}</Link>
                </>
              )}
            </p>
          </div>

          {/* 生成控制台：提示词 + 四宫格结果，纯展示 */}
          <figure className="yc-console">
            <div className="yc-console-bar" aria-hidden>
              <i />
              <i />
              <i />
              <span>yingchuang · generate</span>
            </div>
            <div className="yc-prompt">
              <Sparkles size={16} strokeWidth={2} aria-hidden />
              <p>
                {t('home.promptDemo')}
                <i className="yc-cursor" aria-hidden />
              </p>
              <span className="yc-prompt-go">{t('home.promptGo')}</span>
            </div>
            <div className="yc-console-grid">
              {m.home.showcase.map((label, i) => (
                <div key={label} className="yc-shot">
                  <img src={SHOWCASE[i]} alt="" decoding="async" />
                  <span>{label}</span>
                </div>
              ))}
            </div>
            <figcaption className="yc-console-foot">
              <span className="yc-done">
                <i aria-hidden />
                {t('home.consoleDone')}
              </span>
              <span>{t('home.showcaseNote')}</span>
            </figcaption>
          </figure>
        </section>

        <section className="yc-platforms">
          <p>{t('home.platformsLabel')}</p>
          <div>
            {m.home.platforms.map((p) => (
              <span key={p}>{p}</span>
            ))}
          </div>
        </section>

        <section className="yc-lines" id="products">
          <p className="yc-kicker">01 · PRODUCTS</p>
          <h2>{t('home.linesTitle')}</h2>
          <p className="yc-sub">{t('home.linesSub')}</p>
          <div className="yc-lines-grid">
            {m.home.lines.map((line, i) => {
              const { path, icon: Icon, image } = LINES[i]
              return (
                <button key={line.title} type="button" className="yc-line" onClick={() => goAuthOr(path)}>
                  <span className="yc-line-media">
                    <img src={image} alt="" loading="lazy" decoding="async" />
                  </span>
                  <span className="yc-line-body">
                    <span className="yc-line-head">
                      <span className="yc-line-icon">
                        <Icon size={17} strokeWidth={1.9} aria-hidden />
                      </span>
                      <strong>{line.title}</strong>
                      <ArrowRight className="yc-line-go" size={16} strokeWidth={2} aria-hidden />
                    </span>
                    <span className="yc-line-desc">{line.desc}</span>
                    <span className="yc-line-tags">
                      {line.tags.map((tag) => (
                        <em key={tag}>{tag}</em>
                      ))}
                    </span>
                  </span>
                </button>
              )
            })}
          </div>
          <div className="yc-tools">
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

        <section className="yc-steps">
          <p className="yc-kicker">02 · WORKFLOW</p>
          <h2>{t('home.stepsTitle')}</h2>
          <ol>
            {m.home.steps.map((step, i) => (
              <li key={step.title}>
                <span className="yc-step-no">0{i + 1}</span>
                <strong>{step.title}</strong>
                <p>{step.desc}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="yc-close">
          <h2>
            <span className="yc-grad">{t('home.closeTitle')}</span>
          </h2>
          <p className="yc-sub">{t('home.closeSub')}</p>
          <button type="button" className="yc-btn is-primary" onClick={startCreating}>
            {t('home.start')}
            <ArrowRight size={18} strokeWidth={2.2} aria-hidden />
          </button>
        </section>

        <footer className="yc-foot">
          <div className="yc-foot-brand">
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
      </div>

      <CreateChoiceModal open={chooserOpen} onClose={() => setChooserOpen(false)} />
    </AppShell>
  )
}
