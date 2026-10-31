import { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import { useI18n } from '../../i18n'
import { BRAND_NAME, BRAND_NAME_EN } from '../../lib/siteLinks'
import { resolveNavActive, type NavActive } from '../../lib/siteNavigation'

/* 页面标签标题：按当前路由取导航名，首页保持完整标语 */
const TITLE_KEYS: Partial<Record<NavActive, string>> = {
  kaikou: 'nav.kaikou',
  drama: 'nav.drama',
  canvas: 'nav.canvas',
  kepu: 'nav.kepu',
  video: 'nav.video',
  image: 'nav.image',
  audio: 'nav.audio',
  assets: 'nav.myAssets',
  pricing: 'nav.pricing',
  team: 'nav.team',
  tasks: 'nav.tasks',
  help: 'nav.helpCenter',
  settings: 'nav.profile',
}

/** 路由变化时同步浏览器标签标题，例如「定价 · 开口播」 */
export default function DocumentTitle() {
  const { pathname, search } = useLocation()
  const { t, m, locale } = useI18n()

  useEffect(() => {
    const avatar = pathname === '/studio/new' && new URLSearchParams(search).get('mode') === 'avatar'
    const key = avatar ? 'kaikou' : resolveNavActive(pathname)
    const label = key ? TITLE_KEYS[key] : undefined
    const brand = locale === 'en' ? BRAND_NAME_EN : BRAND_NAME
    const title = pathname === '/' || !label ? m.meta.title : `${t(label)} · ${brand}`
    // 放到下一轮执行，避免被语言 Provider 首次挂载时写回默认标题
    const timer = window.setTimeout(() => { document.title = title }, 0)
    return () => window.clearTimeout(timer)
  }, [pathname, search, t, m, locale])

  return null
}
