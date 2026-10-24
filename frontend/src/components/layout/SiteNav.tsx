import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useEffect, useRef, useState } from 'react'
import {
  ArrowUpRight, ChevronRight, CircleHelp, Clapperboard, CreditCard,
  Images, LayoutDashboard, Menu, Plus, Settings2, UserRound, UsersRound,
  Video, WandSparkles, Workflow, X,
} from 'lucide-react'
import { api } from '../../api'
import type { User } from '../../api'
import UserAvatar from '../UserAvatar'
import BrandMark from '../BrandMark'
import { IconGithub } from '../ui/Icons'
import CreateChoiceModal from '../ui/CreateChoiceModal'
import { USER_UPDATED_EVENT } from '../../lib/userEvents'
import { loadSessionUser } from '../../lib/sessionUser'
import { GITHUB_REPO_URL } from '../../lib/siteLinks'
import { CREATION_NAV, RESOURCE_NAV, resolveNavActive, type NavActive } from '../../lib/siteNavigation'
import { useI18n } from '../../i18n'
import LanguageSwitch from './LanguageSwitch'
import WorkspaceSwitcher from '../../features/teams/WorkspaceSwitcher'
import { useTeamPreview } from '../../features/teams/useTeamPreview'

export type { NavActive } from '../../lib/siteNavigation'

type Props = { active?: NavActive }
type NavItem = (typeof CREATION_NAV)[number] | (typeof RESOURCE_NAV)[number]

const NAV_ICONS = {
  home: LayoutDashboard,
  drama: Clapperboard,
  canvas: Workflow,
  kepu: Video,
  tools: WandSparkles,
  assets: Images,
  team: UsersRound,
  pricing: CreditCard,
}

export default function SiteNav({ active }: Props) {
  const nav = useNavigate()
  const location = useLocation()
  const { t } = useI18n()
  const { selectTeam } = useTeamPreview()
  const [user, setUser] = useState<User | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const sidebarRef = useRef<HTMLElement>(null)
  const menuButtonRef = useRef<HTMLButtonElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)
  const current = resolveNavActive(location.pathname, active)
  const currentItem = [...CREATION_NAV, ...RESOURCE_NAV].find((item) => item.key === current)
  const currentLabel = currentItem ? t(currentItem.label) : current === 'settings' ? t('nav.profile') : current === 'help' ? t('nav.help') : t('nav.workbench')

  useEffect(() => {
    let active = true
    let generation = 0
    /** 只有当前请求的明确 401 可以清理会话，切空间导致的取消不会登出。 */
    function refreshUser() {
      const currentGeneration = ++generation
      if (!localStorage.getItem('token')) { setUser(null); return }
      void loadSessionUser({
        loadUser: () => api.me(),
        getToken: () => localStorage.getItem('token'),
        isCurrent: () => active && currentGeneration === generation,
        onUser: setUser,
        onUnauthorized: () => {
          localStorage.removeItem('token')
          setUser(null)
          window.dispatchEvent(new Event(USER_UPDATED_EVENT))
        },
      })
    }
    /** 用户事件先废弃旧请求，避免旧账号覆盖新登录或最新资料。 */
    const onUserUpdated = (event: Event) => {
      generation += 1
      const detail = (event as CustomEvent<User>).detail
      if (detail && localStorage.getItem('token')) setUser(detail)
      else refreshUser()
    }
    window.addEventListener(USER_UPDATED_EVENT, onUserUpdated)
    refreshUser()
    return () => { active = false; generation += 1; window.removeEventListener(USER_UPDATED_EVENT, onUserUpdated) }
  }, [])

  useEffect(() => { setMenuOpen(false) }, [location.pathname, location.search])

  useEffect(() => {
    const desktop = window.matchMedia('(min-width: 1001px)')
    function closeOnDesktop() { if (desktop.matches) setMenuOpen(false) }
    desktop.addEventListener('change', closeOnDesktop)
    return () => desktop.removeEventListener('change', closeOnDesktop)
  }, [])

  useEffect(() => {
    if (!menuOpen) return
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    closeButtonRef.current?.focus()

    function keepFocusInside(event: KeyboardEvent) {
      // 独立弹窗接管自己的键盘事件；普通下拉仍归属当前抽屉。
      const modal = document.querySelector<HTMLElement>('[role="dialog"][aria-modal="true"]:not(#pf-app-sidebar)')
      if (modal && !sidebarRef.current?.contains(modal)) return
      if (event.key === 'Escape') {
        event.preventDefault()
        setMenuOpen(false)
        return
      }
      if (event.key !== 'Tab' || !sidebarRef.current) return
      const focusable = Array.from(sidebarRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not(:disabled), summary, input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
      )).filter((element) => element.getClientRects().length > 0 && !element.closest('[inert]'))
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (!first || !last) { event.preventDefault(); return }
      const focused = document.activeElement
      if (!sidebarRef.current.contains(focused) || (event.shiftKey && focused === first)) {
        event.preventDefault()
        ;(event.shiftKey ? last : first).focus()
      } else if (!event.shiftKey && focused === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', keepFocusInside)
    return () => {
      document.body.style.overflow = previousOverflow
      document.removeEventListener('keydown', keepFocusInside)
      menuButtonRef.current?.focus()
    }
  }, [menuOpen])

  function goCreate() {
    setMenuOpen(false)
    if (!user && !localStorage.getItem('token')) {
      nav('/auth?next=/')
      return
    }
    setCreateOpen(true)
  }

  function renderLink(item: NavItem) {
    const Icon = NAV_ICONS[item.key]
    const selected = current === item.key
    return <Link
      key={item.key}
      to={item.path}
      className={`pf-sidebar-link${selected ? ' is-active' : ''}`}
      aria-current={selected ? 'page' : undefined}
      onClick={() => {
        if (item.key !== 'team' && item.key !== 'home') selectTeam(null)
        setMenuOpen(false)
      }}
    >
      <Icon size={19} strokeWidth={1.8} aria-hidden />
      <span>{t(item.label)}</span>
      {selected && <ChevronRight size={15} className="pf-sidebar-link-arrow" aria-hidden />}
    </Link>
  }

  return <>
    <header className="pf-app-mobile-header">
      <button ref={menuButtonRef} type="button" className="pf-sidebar-icon-button" aria-label={t('nav.openMenu')} aria-controls="pf-app-sidebar" aria-expanded={menuOpen} onClick={() => setMenuOpen(true)}><Menu size={21} aria-hidden /></button>
      <BrandMark />
      <span className="pf-mobile-current">{currentLabel}</span>
      <button type="button" className="pf-mobile-create" aria-label={t('nav.startCreate')} onClick={goCreate}><Plus size={20} aria-hidden /></button>
    </header>
    {menuOpen && <button type="button" className="pf-sidebar-backdrop" aria-label={t('nav.closeMenu')} tabIndex={-1} onClick={() => setMenuOpen(false)} />}
    <aside ref={sidebarRef} id="pf-app-sidebar" className={`pf-app-sidebar${menuOpen ? ' is-open' : ''}`} role={menuOpen ? 'dialog' : undefined} aria-modal={menuOpen ? true : undefined} aria-label={t('nav.main')}>
      <div className="pf-sidebar-top">
        <div className="pf-sidebar-brand"><BrandMark /><button ref={closeButtonRef} type="button" className="pf-sidebar-icon-button pf-sidebar-close" aria-label={t('nav.closeMenu')} onClick={() => setMenuOpen(false)}><X size={19} aria-hidden /></button></div>
        <div className="pf-sidebar-workspace"><WorkspaceSwitcher /></div>
        <button type="button" className="pf-sidebar-create" onClick={goCreate}><Plus size={19} aria-hidden /><span>{t('nav.startCreate')}</span><ArrowUpRight size={17} aria-hidden /></button>
      </div>
      <nav className="pf-sidebar-navigation" aria-label={t('nav.main')}>
        <div className="pf-sidebar-group"><span className="pf-sidebar-group-label">{t('nav.creation')}</span>{CREATION_NAV.map(renderLink)}</div>
        <div className="pf-sidebar-group"><span className="pf-sidebar-group-label">{t('nav.resources')}</span>{RESOURCE_NAV.map(renderLink)}</div>
      </nav>
      <div className="pf-sidebar-bottom">
        <div className="pf-sidebar-utilities">
          <Link to="/help" className={current === 'help' ? 'is-active' : undefined} aria-current={current === 'help' ? 'page' : undefined} onClick={() => setMenuOpen(false)}><CircleHelp size={17} aria-hidden /><span>{t('nav.help')}</span></Link>
          <a href={GITHUB_REPO_URL} target="_blank" rel="noopener noreferrer" title={t('nav.githubTitle')} onClick={() => setMenuOpen(false)}><IconGithub size={17} /><span>{t('nav.github')}</span></a>
        </div>
        <div className="pf-sidebar-language"><span>{t('nav.language')}</span><LanguageSwitch /></div>
        <Link to={user ? '/settings' : '/auth?next=/'} className={`pf-sidebar-account${current === 'settings' ? ' is-active' : ''}`} aria-current={current === 'settings' ? 'page' : undefined} onClick={() => setMenuOpen(false)}>
          {user ? <UserAvatar user={user} size="sm" /> : <span className="pf-sidebar-account-guest"><UserRound size={19} aria-hidden /></span>}
          <span><strong>{user?.nickname || t('nav.login')}</strong><small>{user ? t('nav.profile') : t('nav.loginHint')}</small></span>
          <Settings2 size={17} aria-hidden />
        </Link>
      </div>
    </aside>
    <CreateChoiceModal open={createOpen} onClose={() => setCreateOpen(false)} />
  </>
}
