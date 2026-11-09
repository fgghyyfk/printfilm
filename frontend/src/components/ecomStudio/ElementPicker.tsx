import { useEffect, useState } from 'react'
import { ImagePlus, Loader2, Plus, Search, Trash2, X } from 'lucide-react'
import {
  createEcomElement, deleteEcomElement, listEcomElements, recommendEcomElements, type EcomElement,
} from '../../api/ecomElements'
import { resolveToolMediaUrl } from '../../api/tools'
import { ECOM_UPLOAD_ACCEPT } from '../../lib/ecomSet'
import { useGenerationText } from '../../lib/generationMedia'
import { dialog } from '../../lib/dialog'

type Props = {
  productId: number | null
  industry: string
  /** 已选元素（保持顺序） */
  value: EcomElement[]
  onChange: (next: EcomElement[]) => void
}

const MAX = 12

/** 画面元素：推荐（按商品资料）+ 分类浏览/搜索 + 我的元素（一句话或实物照片）；不选则自动推荐 */
export default function ElementPicker({ productId, industry, value, onChange }: Props) {
  const text = useGenerationText()
  /* 推荐与列表 */
  const [rec, setRec] = useState<EcomElement[] | null>(null)
  const [tab, setTab] = useState('rec')
  const [q, setQ] = useState('')
  const [items, setItems] = useState<EcomElement[] | null>(null)
  const [cats, setCats] = useState<{ id: string; name: string }[]>([])
  const [error, setError] = useState('')
  /* 新建我的元素 */
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState({ name: '', category: 'vessel', prompt: '' })
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const picked = new Set(value.map((e) => e.key))

  useEffect(() => {
    const c = new AbortController()
    void recommendEcomElements(productId, industry, c.signal).then((r) => setRec(r.items))
      .catch((err) => { if (!c.signal.aborted) setError(err instanceof Error ? err.message : text('加载失败', 'Load failed')) })
    return () => c.abort()
  }, [productId, industry, text])

  useEffect(() => {
    if (tab === 'rec') return
    const c = new AbortController()
    setItems(null)
    const t = setTimeout(() => {
      void listEcomElements({ category: tab === 'mine' ? '' : tab, mine: tab === 'mine', q: q.trim(), industry }, c.signal)
        .then((r) => { setItems(r.items); setCats(r.categories) })
        .catch((err) => { if (!c.signal.aborted) setError(err instanceof Error ? err.message : text('加载失败', 'Load failed')) })
    }, q ? 250 : 0)
    return () => { c.abort(); clearTimeout(t) }
  }, [tab, q, industry, text])

  // 首次进来拿分类列表
  useEffect(() => {
    const c = new AbortController()
    void listEcomElements({ pageSize: 1 }, c.signal).then((r) => setCats(r.categories)).catch(() => undefined)
    return () => c.abort()
  }, [])

  // 勾选 / 取消
  function toggle(el: EcomElement) {
    if (picked.has(el.key)) onChange(value.filter((e) => e.key !== el.key))
    else if (value.length < MAX) onChange([...value, el])
    else setError(text(`最多选 ${MAX} 个元素`, `Up to ${MAX}`))
  }

  // 保存我的元素并选上
  async function save() {
    if (!draft.name.trim()) return setError(text('请填写元素名称', 'Enter a name'))
    setBusy(true); setError('')
    try {
      const el = await createEcomElement({ ...draft, name: draft.name.trim(), file })
      onChange([...value, el].slice(0, MAX))
      setDraft({ name: '', category: 'vessel', prompt: '' }); setFile(null); setAdding(false)
      if (tab === 'mine') setItems((prev) => [el, ...(prev || [])])
    } catch (err) { setError(err instanceof Error ? err.message : text('保存失败', 'Failed')) }
    finally { setBusy(false) }
  }

  // 删除我的元素
  async function remove(el: EcomElement) {
    if (!(await dialog.confirm(text(`删除元素「${el.name}」？已生成的图不受影响。`, `Delete ${el.name}?`)))) return
    try {
      await deleteEcomElement(el.key)
      setItems((prev) => (prev || []).filter((e) => e.key !== el.key))
      onChange(value.filter((e) => e.key !== el.key))
    } catch (err) { setError(err instanceof Error ? err.message : text('删除失败', 'Failed')) }
  }

  const list = tab === 'rec' ? rec : items
  const chip = (el: EcomElement) => <button key={el.key} type="button" className={`pf-es-el${picked.has(el.key) ? ' active' : ''}`}
    onClick={() => toggle(el)} title={el.prompt}>
    {el.image_url ? <img src={resolveToolMediaUrl(el.image_url)} alt="" /> : null}<span>{el.name}</span>
    {tab === 'mine' ? <Trash2 size={12} className="pf-es-el-del" onClick={(e) => { e.stopPropagation(); void remove(el) }} /> : null}
  </button>

  return <div className="pf-tool-panel pf-es-elements">
    <div className="pf-es-product-head">
      <div><strong>{text('画面元素', 'Elements')}</strong>
        <span className="pf-muted"> · {text(`已选 ${value.length}/${MAX}；不选则按商品资料自动搭配，每张图会错开不同元素与构图`, `${value.length}/${MAX}`)}</span></div>
      <button type="button" className="pf-es-link" onClick={() => setAdding((v) => !v)}><Plus size={14} />{text('新建我的元素', 'New element')}</button>
    </div>
    {value.length ? <div className="pf-es-el-row">{value.map((el) => <span key={el.key} className="pf-es-el active">
      {el.image_url ? <img src={resolveToolMediaUrl(el.image_url)} alt="" /> : null}<span>{el.name}</span>
      <X size={12} onClick={() => toggle(el)} aria-label={text('移除', 'Remove')} />
    </span>)}</div> : null}
    {adding ? <div className="pf-es-el-form">
      <input className="pf-ecom-input" placeholder={text('名称，如：品牌礼盒、白色山茶花', 'Name')} maxLength={32} value={draft.name}
        onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
      <select className="pf-ecom-input" value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value })}>
        {cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
      <input className="pf-ecom-input" placeholder={text('补充描述（可选），如：金色缎带的白色礼盒', 'Description (optional)')} maxLength={200}
        value={draft.prompt} onChange={(e) => setDraft({ ...draft, prompt: e.target.value })} />
      <label className="pf-es-link"><ImagePlus size={14} />{file ? file.name : text('上传实物照（可选，AI 会照着画）', 'Photo (optional)')}
        <input type="file" hidden accept={ECOM_UPLOAD_ACCEPT} onChange={(e) => { setFile(e.target.files?.[0] || null); e.target.value = '' }} />
      </label>
      <button type="button" className="pf-es-link" disabled={busy} onClick={() => void save()}>
        {busy ? <Loader2 size={14} className="pf-tool-spin" /> : null}{text('保存并选上', 'Save')}</button>
    </div> : null}
    <div className="pf-tool-chips pf-es-el-tabs">
      {[{ id: 'rec', name: text('推荐', 'Suggested') }, ...cats, { id: 'mine', name: text('我的元素', 'Mine') }].map((c) =>
        <button key={c.id} type="button" className={tab === c.id ? 'active' : ''} onClick={() => setTab(c.id)}>{c.name}</button>)}
    </div>
    {tab !== 'rec' ? <label className="pf-es-el-search"><Search size={14} />
      <input placeholder={text('搜索元素', 'Search')} value={q} maxLength={20} onChange={(e) => setQ(e.target.value)} /></label> : null}
    {error ? <p className="pf-tool-error">{error}</p> : null}
    {!list ? <p className="pf-muted"><Loader2 size={14} className="pf-tool-spin" /></p>
      : !list.length ? <p className="pf-muted">{tab === 'mine' ? text('还没有我的元素，点右上角新建。', 'No elements yet.') : text('没有匹配的元素', 'Nothing found')}</p>
        : <div className="pf-es-el-row">{list.map(chip)}</div>}
  </div>
}
