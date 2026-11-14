import Button from '../../components/ui/Button'

/** 团队列表公用分页控件，页数由服务器 total 决定。 */
export function TeamPagination({ page, total, size = 12, onChange }: { page: number; total: number; size?: number; onChange: (page: number) => void }) {
  return total > size ? <div className="pf-team-modal-actions"><span className="pf-team-muted">共 {total} 项 · 第 {page} / {Math.ceil(total / size)} 页</span><Button size="sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>上一页</Button><Button size="sm" disabled={page * size >= total} onClick={() => onChange(page + 1)}>下一页</Button></div> : null
}
