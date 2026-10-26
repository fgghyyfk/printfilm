/** 导航入口：公开产品位于顶栏，个人资源收进头像菜单。 */
import { SHORT_VIDEO_LIST_PATH } from './shortVideoRoutes.ts'

export type NavActive = 'home' | 'canvas' | 'drama' | 'kepu' | 'video' | 'image' | 'audio' | 'assets' | 'pricing' | 'templates' | 'studio' | 'history' | 'team' | 'tasks' | 'help' | 'settings'

/** 首页是全站总览入口，独立于具体创作产品分组。 */
export const HOME_NAV = { key: 'home', path: '/', label: 'nav.home' } as const

export const CREATION_NAV = [
  { key: 'drama', path: '/drama', label: 'nav.drama' },
  { key: 'canvas', path: '/canvas', label: 'nav.canvas' },
  { key: 'kepu', path: SHORT_VIDEO_LIST_PATH, label: 'nav.kepu' },
] as const

export const RESOURCE_NAV = [
  { key: 'pricing', path: '/pricing', label: 'nav.pricing' },
] as const

export const PERSONAL_NAV = [
  { key: 'tasks', path: '/tasks', label: 'nav.tasks' },
  { key: 'assets', path: '/assets', label: 'nav.myAssets' },
] as const

/** 路由段精确匹配，避免 /canvas-unknown 等非业务地址误高亮。 */
export function resolveNavActive(pathname: string, active?: NavActive): NavActive | undefined {
  if (active === 'studio' || active === 'templates' || active === 'history') return 'kepu'
  if (active) return active
  const within = (base: string) => pathname === base || pathname.startsWith(`${base}/`)
  if (within('/canvas')) return 'canvas'
  if (within('/teams') || within('/team-invitations')) return 'team'
  if (within('/drama/assets') || within('/assets')) return 'assets'
  if (within('/drama')) return 'drama'
  if (['/studio', '/templates', '/history'].some(within)) return 'kepu'
  if (within('/tools/video')) return 'video'
  if (within('/tools/image')) return 'image'
  if (within('/tools/audio')) return 'audio'
  if (within('/tasks')) return 'tasks'
  if (within('/pricing')) return 'pricing'
  if (within('/help')) return 'help'
  if (within('/settings')) return 'settings'
  if (pathname === '/') return 'home'
  return undefined
}
