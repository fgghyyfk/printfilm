/** 全站主题：管理员在后台切换。启动时先用缓存渲染（index.html 内联脚本），再向服务端同步。 */

export const SITE_THEMES = ['daylight', 'studio'] as const
export type SiteTheme = (typeof SITE_THEMES)[number]
export const DEFAULT_SITE_THEME: SiteTheme = 'daylight'
export const SITE_THEME_STORAGE_KEY = 'kk-site-theme'
export const SITE_THEME_PREVIEW_PARAM = 'preview_theme'

const THEME_CHROME: Record<SiteTheme, { color: string; icon: string }> = {
  daylight: { color: '#f8f7f3', icon: '/logo-daylight.svg' },
  studio: { color: '#17130e', icon: '/favicon.svg' },
}

/** 把任意输入收敛成可用主题；不认识的回退默认。 */
export function normalizeSiteTheme(value: unknown): SiteTheme {
  return SITE_THEMES.includes(value as SiteTheme) ? (value as SiteTheme) : DEFAULT_SITE_THEME
}

/** 地址里的预览主题；无效返回 null。后台“预览”用，只影响当前页面，不保存。 */
export function previewThemeFromSearch(search: string): SiteTheme | null {
  const raw = new URLSearchParams(search).get(SITE_THEME_PREVIEW_PARAM)
  return raw && SITE_THEMES.includes(raw as SiteTheme) ? (raw as SiteTheme) : null
}

/** 应用主题到页面：根节点属性、浏览器主题色和网站图标。 */
export function applySiteTheme(theme: SiteTheme): void {
  const root = document.documentElement
  root.dataset.theme = theme
  const chrome = THEME_CHROME[theme]
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', chrome.color)
  document.querySelector('link[rel="icon"]')?.setAttribute('href', chrome.icon)
}

/** 启动时同步：预览优先；否则读服务端主题并缓存。请求失败时保持当前主题，不影响使用。 */
export async function syncSiteTheme(apiBase: string): Promise<void> {
  const preview = previewThemeFromSearch(window.location.search)
  if (preview) {
    applySiteTheme(preview)
    return
  }
  try {
    const res = await fetch(`${apiBase}/api/site/appearance`)
    if (!res.ok) return
    const theme = normalizeSiteTheme(((await res.json()) as { theme?: unknown }).theme)
    applySiteTheme(theme)
    try {
      localStorage.setItem(SITE_THEME_STORAGE_KEY, theme)
    } catch {
      // 存储不可用时只影响下次首屏
    }
  } catch {
    // 网络失败时沿用缓存主题
  }
}
