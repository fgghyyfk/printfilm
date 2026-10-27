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
export function PublishGuide({ recruit = false, saleType = 'copyright_contact' }: { recruit?: boolean; saleType?: 'free' | 'copyright_contact' | 'platform_unlock' }) {
  const steps = recruit ? [['说明需求', '写清用途、预算和交付要求。'], ['提交审核', '审核通过后进入招募广场。'], ['联系客服', '创作者先联系客服表达接单意向。'], ['线下合作', '客服协调需求、预算、合同及交付。']] : [['完善作品', '上传视频与封面，填写作品介绍。'], ['确认版权', 'PrintFilm 作品使用有效授权函，其他平台作品提交证明材料；暂缺材料可联系客服。'], ['提交审核', '作品与版权材料一并提交，由管理员统一审核。'], ['上架发布', '审核通过后，由你主动上架。']]
  const saleNotice = saleType === 'free' ? '上架后可完整免费观看。免费不代表可任意商用，使用仍须遵守作者授权范围。' : saleType === 'platform_unlock' ? '观众按标价购买所购版本的永久在线播放权益，不包含版权转让；创作者按已确定的比例获得收益。' : '版权合作参考价供洽谈使用。最终价格、授权范围、转让手续与相关费用请和客服确认，咨询不代表成交。'
  return <aside className="pf-market-guide"><div className="pf-market-guide-heading"><ShieldCheck size={21} /><h2>{recruit ? '从需求到合作' : '从作品到市场'}</h2></div><ol>{steps.map(([title, description], index) => <li key={title}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{title}</strong><p>{description}</p></div></li>)}</ol><div className="pf-market-guide-note"><strong>{recruit ? '先明确，再合作' : '了解收益与授权'}</strong><p>{recruit ? '创作者通过平台客服接单。具体合作条件在线下协商确认，咨询不代表已接单或成交。' : saleNotice}</p>{!recruit && <p>版权证明不能代替音乐、字体、肖像等素材的必要授权。</p>}</div></aside>
}

