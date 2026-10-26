import type { ReactNode } from 'react'
import { ArrowLeft, ArrowUpRight, FolderOpen, ShieldCheck } from 'lucide-react'
import { Link } from 'react-router-dom'
import Button from '../../components/ui/Button'

// 模块内统一页头，明确当前任务和返回路径。
export function PageHeading({ title, description, back, backLabel = '返回广场' }: { title: string; description: string; back?: string; backLabel?: string }) {
  return <header className="pf-market-page-heading">{back && <Link className="pf-market-back" to={back}><ArrowLeft size={15} />{backLabel}</Link>}<h1>{title}</h1><p>{description}</p></header>
}

// 空数据、空搜索和个人工作台共用真实空态，不填充示例作品。
export function EmptyState({ title, description, action, to, children }: { title: string; description: string; action?: string; to?: string; children?: ReactNode }) {
  return <div className="pf-market-empty"><div className="pf-market-empty-icon" aria-hidden><FolderOpen size={30} strokeWidth={1.4} /></div><h3>{title}</h3><p>{description}</p>{action && to && <Button to={to} variant="dark" icon>{action}<ArrowUpRight size={16} /></Button>}{children}</div>
}

// 发布表单按内容分组，数字仅表示填写顺序。
export function FormSection({ step, title, description, children }: { step: string; title: string; description?: string; children: ReactNode }) {
  return <section className="pf-market-form-section"><header><span>{step}</span><div><h2>{title}</h2>{description && <p>{description}</p>}</div></header><div className="pf-market-fields">{children}</div></section>
}

// 侧栏展示实际发布规则，不把流程说明显示为已完成状态。
export function PublishGuide({ recruit = false }: { recruit?: boolean }) {
  const steps = recruit ? [['说明需求', '写清用途、预算和交付要求。'], ['内容审核', '审核通过后进入招募广场。'], ['联系客服', '创作者先联系客服表达接单意向。'], ['线下合作', '客服协调需求、预算、合同及交付。']] : [['上传与介绍', '上传视频，完善作品资料。'], ['权属核验', '本平台签授权函，其他平台交证明，无证明联系客服。'], ['内容审核', '审核通过后，由你主动上架。'], ['客服撮合', '授权、合同与交付在线下确认。']]
  return <aside className="pf-market-guide"><div className="pf-market-guide-heading"><ShieldCheck size={21} /><h2>{recruit ? '从需求到合作' : '从作品到市场'}</h2></div><ol>{steps.map(([title, description], index) => <li key={title}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{title}</strong><p>{description}</p></div></li>)}</ol><div className="pf-market-guide-note"><strong>{recruit ? '先明确，再合作' : '让授权更清楚'}</strong><p>{recruit ? '创作者通过平台客服接单。具体合作条件在线下协商确认，咨询不代表已接单或成交。' : '登记证书不能代替素材授权。音乐、字体、肖像等涉及的权利，需要逐项说明。'}</p></div></aside>
}

