/** 主导航路由：创作产品、独立生成与资源协作共用同一份入口配置。 */
import { SHORT_VIDEO_LIST_PATH } from './shortVideoRoutes.ts'

export type NavActive = 'home' | 'canvas' | 'drama' | 'kepu' | 'video' | 'image' | 'tools' | 'assets' | 'pricing' | 'templates' | 'studio' | 'history' | 'team' | 'tasks' | 'help' | 'settings'

export const CREATION_NAV = [
  { key: 'home', path: '/', label: 'nav.workbench' },
  { key: 'drama', path: '/drama', label: 'nav.drama' },
  { key: 'canvas', path: '/canvas', label: 'nav.canvas' },
  { key: 'kepu', path: SHORT_VIDEO_LIST_PATH, label: 'nav.kepu' },
  { key: 'video', path: '/tools/video', label: 'nav.video' },
  { key: 'image', path: '/tools/image', label: 'nav.image' },
  { key: 'tools', path: '/tools', label: 'nav.tools' },
] as const

export const RESOURCE_NAV = [
  { key: 'tasks', path: '/tasks', label: 'nav.tasks' },
  { key: 'assets', path: '/assets', label: 'nav.assets' },
  { key: 'team', path: '/teams', label: 'nav.team' },
  { key: 'pricing', path: '/pricing', label: 'nav.pricing' },
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
  if (within('/tools')) return 'tools'
  if (within('/tasks')) return 'tasks'
  if (within('/pricing')) return 'pricing'
  if (within('/help')) return 'help'
  if (within('/settings')) return 'settings'
  if (pathname === '/') return 'home'
  return undefined
}
