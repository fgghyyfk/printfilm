import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { I18nProvider } from './i18n'
import './index.css'

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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </StrictMode>,
)
