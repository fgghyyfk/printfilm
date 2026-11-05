import { useEffect, useState } from 'react'
import { Download, ExternalLink, ImageIcon, Loader2, Rows3, X } from 'lucide-react'
import { downloadEcomSetLong, downloadEcomSetZip, listEcomSets, type EcomSet, type EcomSetItem } from '../../api/ecomSet'
import { resolveToolMediaUrl } from '../../api/tools'
import { canDownloadSet, defaultZipName } from '../../lib/ecomSet'
import { useGenerationText } from '../../lib/generationMedia'
import { pageCountOf } from '../../lib/pagination'
import Pagination from '../ui/Pagination'
import Button from '../ui/Button'
import './ecomSetBatches.css'

type Props = { onOpen: (id: number) => void; pageSize?: number }

/** 套图按生成批次展示：每批一行，铺开这一批的全部成图（大图），可放大预览、打开、下载 ZIP / 长图 */
export default function EcomSetBatches({ onOpen, pageSize = 5 }: Props) {
  const text = useGenerationText()
  /* 分页列表三态 + 下载中 + 放大预览 */
  const [page, setPage] = useState(1)
  const [items, setItems] = useState<EcomSet[] | null>(null)
  const [total, setTotal] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [preview, setPreview] = useState<{ set: EcomSet; item: EcomSetItem } | null>(null)

  useEffect(() => {
    let cancelled = false
    setError('')
    listEcomSets(page, pageSize)
      .then((data) => { if (!cancelled) { setItems(data.items); setTotal(data.total) } })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : text('加载失败', 'Load failed')) })
    return () => { cancelled = true }
  }, [page, pageSize, text])

  // 下载 ZIP 或长图
  async function download(set: EcomSet, kind: 'zip' | 'long') {
    setBusy(`${kind}${set.id}`); setError('')
    try {
      if (kind === 'zip') await downloadEcomSetZip(set.id, defaultZipName(set))
      else await downloadEcomSetLong(set.id, defaultZipName(set).replace(/\.zip$/, '-详情长图.jpg'))
    } catch (err) { setError(err instanceof Error ? err.message : text('下载失败', 'Download failed')) }
    finally { setBusy('') }
  }

  if (error && !items) return <p className="pf-tool-error">{error}</p>
  if (!items) return <p className="pf-muted"><Loader2 size={16} className="pf-tool-spin" /> {text('加载中…', 'Loading…')}</p>
  if (!items.length) return <p className="pf-muted">{text('还没有生成过电商图套图。', 'No image sets yet.')}</p>

  return <section className="pf-batches">
    {error ? <p className="pf-tool-error">{error}</p> : null}
    {items.map((set) => {
      const done = set.items.filter((it) => it.final_url)
      const info = set.styled
      const meta = [info?.color_name, info?.material_label, info?.size_label, set.created_at && new Date(set.created_at).toLocaleString()].filter(Boolean)
      return <article key={set.id} className="pf-batch">
        <header className="pf-batch-head">
          <div>
            <strong>{set.name}</strong>
            <span className="pf-muted"> · {text(`第 ${set.id} 批`, `Batch #${set.id}`)} · {text(`${done.length}/${set.items.length} 张`, `${done.length}/${set.items.length}`)}{meta.length ? ` · ${meta.join(' · ')}` : ''}</span>
          </div>
          <div className="pf-batch-actions">
            <Button variant="text" size="sm" onClick={() => onOpen(set.id)}><ExternalLink size={14} />{text('打开编辑', 'Open')}</Button>
            <Button variant="outline" size="sm" disabled={!canDownloadSet(set) || !!busy} onClick={() => void download(set, 'long')}>
              {busy === `long${set.id}` ? <Loader2 size={14} className="pf-tool-spin" /> : <Rows3 size={14} />}{text('长图', 'Long image')}</Button>
            <Button variant="lime" size="sm" disabled={!canDownloadSet(set) || !!busy} onClick={() => void download(set, 'zip')}>
              {busy === `zip${set.id}` ? <Loader2 size={14} className="pf-tool-spin" /> : <Download size={14} />}{text('下载 ZIP', 'ZIP')}</Button>
          </div>
        </header>
        <div className="pf-batch-strip">
          {set.items.map((it) => <button key={it.idx} type="button" className="pf-batch-item" disabled={!it.final_url}
            onClick={() => it.final_url && setPreview({ set, item: it })} title={it.slot_label}>
            {it.final_url ? <img src={resolveToolMediaUrl(it.final_url)} alt={it.slot_label} loading="lazy" />
              : <span className="pf-batch-ph">{it.status === 'failed' ? text('失败', 'Failed') : it.status === 'planned' ? text('未生成', 'Not generated') : <Loader2 size={18} className="pf-tool-spin" />}</span>}
            <span>{it.idx + 1}. {it.slot_label}</span>
          </button>)}
          {!set.items.length ? <span className="pf-batch-ph"><ImageIcon size={18} /></span> : null}
        </div>
      </article>
    })}
    <Pagination page={page} pageCount={pageCountOf(total, pageSize)} total={total} pageSize={pageSize} onChange={setPage} />
    {preview ? <div className="pf-batch-lightbox" role="dialog" aria-modal="true" onClick={() => setPreview(null)}>
      <button type="button" className="pf-batch-close" aria-label={text('关闭', 'Close')} onClick={() => setPreview(null)}><X size={20} /></button>
      <img src={resolveToolMediaUrl(preview.item.final_url)} alt={preview.item.slot_label} onClick={(e) => e.stopPropagation()} />
      <p>{preview.set.name} · {preview.item.idx + 1}. {preview.item.slot_label}</p>
    </div> : null}
  </section>
}
