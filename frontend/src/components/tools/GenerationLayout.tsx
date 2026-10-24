import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { AudioLines, FolderOpen, Image, ListTodo, Video } from 'lucide-react'
import AppShell from '../layout/AppShell'
import { useGenerationText } from '../../lib/generationMedia'
import '../../styles/generation.css'
import '../../styles/generation-controls.css'

export default function GenerationLayout({ children, tasks = false }: { children: ReactNode; tasks?: boolean }) {
  const text = useGenerationText()
  const entries = [
    { to: '/tools/video', label: text('视频', 'Video'), icon: Video },
    { to: '/tools/image', label: text('图片', 'Image'), icon: Image },
    { to: '/tools/audio', label: text('音频', 'Audio'), icon: AudioLines },
    { to: '/tasks', label: text('任务', 'Tasks'), icon: ListTodo },
    { to: '/assets', label: text('资产', 'Assets'), icon: FolderOpen },
  ]
  return <AppShell active={tasks ? 'tasks' : 'tools'} wide hideFooter>
    <div className="gen-layout">
      <nav className="gen-rail" aria-label={text('生成工作台', 'Generation studio')}>
        <span className="gen-rail-mark" aria-hidden>✦</span>
        {entries.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => isActive ? 'is-active' : ''}>
          <Icon size={21} strokeWidth={1.8} /><span>{label}</span>
        </NavLink>)}
      </nav>
      <div className="gen-main">{children}</div>
    </div>
  </AppShell>
}
