import type { ReactNode } from 'react'
import { ArrowUpRight, Clapperboard, LayoutGrid } from 'lucide-react'
import './project-card.css'

export type ProjectCardProps = {
  title: string
  coverUrl?: string
  kind: 'drama' | 'canvas' | 'short-video'
  category?: string
  status?: { label: string; tone: 'draft' | 'producing' | 'completed' | 'neutral' }
  description?: string
  progress?: { label: string; value: number; total: number }
  meta?: ReactNode
  footer?: ReactNode
  actions?: ReactNode
  menu?: ReactNode
  selection?: { checked: boolean; visible: boolean; onChange: () => void; label: string }
  onOpen: () => void
  openLabel?: string
  className?: string
}

/** 所有创作空间使用同一卡片。打开、菜单及选择分别是独立控件。 */
export function ProjectCard({ title, coverUrl, kind, category, status, description, progress, meta, footer, actions, menu, selection, onOpen, openLabel, className = '' }: ProjectCardProps) {
  const Mark = kind === 'canvas' ? LayoutGrid : Clapperboard
  const ratio = progress && progress.total > 0 ? Math.max(0, Math.min(100, progress.value / progress.total * 100)) : 0
  return (
    <article className={`pf-shared-project-card pf-shared-project-card--${kind}${selection?.checked ? ' is-selected' : ''} ${className}`}>
      <button type="button" className="pf-shared-project-open-target" aria-label={openLabel || `打开 ${title}`} title={title} onClick={onOpen} />
      <div className={`pf-shared-project-cover${coverUrl ? ' has-image' : ''}`}>
        {coverUrl ? <img src={coverUrl} alt="" loading="lazy" /> : <Mark className="pf-shared-project-cover-mark" size={104} strokeWidth={0.8} aria-hidden="true" />}
        <span className="pf-shared-project-cover-shade" />
        {status && <span className={`pf-shared-project-status pf-shared-project-status--${status.tone}${selection ? ' has-selection' : ''}`}><i aria-hidden="true" />{status.label}</span>}
        <div className="pf-shared-project-cover-title">
          {category && <span>{category}</span>}
          <h3 title={title}>{title}</h3>
        </div>
        <span className="pf-shared-project-open-icon" aria-hidden="true"><ArrowUpRight size={19} /></span>
      </div>
      <div className="pf-shared-project-body">
        {description && <p className="pf-shared-project-description">{description}</p>}
        {meta && <div className="pf-shared-project-meta">{meta}</div>}
        {progress && <div className="pf-shared-project-progress-block">
          <div className="pf-shared-project-progress-meta"><span>{progress.label}</span><strong>{progress.value}<span> / {progress.total}</span></strong></div>
          <div className="pf-shared-project-progress" role="progressbar" aria-label={progress.label} aria-valuemin={0} aria-valuemax={Math.max(progress.total, 1)} aria-valuenow={Math.max(0, Math.min(progress.value, progress.total))}><span style={{ width: `${ratio}%` }} /></div>
        </div>}
        {footer && <div className="pf-shared-project-footer">{footer}</div>}
      </div>
      {selection && <label className={`pf-shared-project-selection${selection.visible || selection.checked ? ' is-visible' : ''}`} title={selection.label}><input type="checkbox" checked={selection.checked} aria-label={selection.label} onChange={selection.onChange} /></label>}
      {menu && <div className="pf-shared-project-menu">{menu}</div>}
      {actions && <div className="pf-shared-project-actions">{actions}</div>}
    </article>
  )
}

export function ProjectGrid({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`pf-shared-project-grid ${className}`}>{children}</div>
}
