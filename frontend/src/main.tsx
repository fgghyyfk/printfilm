import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { I18nProvider } from './i18n'
// 字体自托管（@fontsource，按 unicode-range 分片按需加载），避免国内访问 Google Fonts 卡首屏
import '@fontsource/noto-sans-sc/400.css'
import '@fontsource/noto-sans-sc/500.css'
import '@fontsource/noto-sans-sc/700.css'
import '@fontsource/noto-sans-sc/900.css'
import '@fontsource/noto-serif-sc/500.css'
import '@fontsource/noto-serif-sc/700.css'
import '@fontsource/space-grotesk/500.css'
import '@fontsource/space-grotesk/600.css'
import '@fontsource/space-grotesk/700.css'
import './index.css'
import { API_BASE } from './api/client'
import { syncSiteTheme } from './lib/siteTheme'

/* 发版后旧页面再按需加载会找不到已删除的分包：自动刷新一次拿新版本（30 秒内不重复刷新） */
window.addEventListener('vite:preloadError', (event) => {
  try {
    const last = Number(sessionStorage.getItem('pf-chunk-reload') || 0)
    if (Date.now() - last < 30_000) return
    sessionStorage.setItem('pf-chunk-reload', String(Date.now()))
  } catch {
    // 存储不可用时直接刷新
  }
  event.preventDefault()
  window.location.reload()
})

void syncSiteTheme(API_BASE)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </StrictMode>,
)
