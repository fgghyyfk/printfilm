import { useEffect, useRef, useState } from 'react'
import { Ban, Check, Loader2, RefreshCw } from 'lucide-react'
import { listEcomScenes, type EcomScene } from '../../api/ecomScenes'
import { resolveToolMediaUrl } from '../../api/tools'
import { useGenerationText } from '../../lib/generationMedia'
import Button from '../ui/Button'

const PAGE_SIZE = 24

type Props = {
  industries: { id: string; name: string }[]
  industry: string
  onIndustry: (id: string) => void
  /** 当前选中的场景 key；空字符串 = 不指定场景 */
  sceneKey: string
  onPick: (scene: { key: string; name: string } | null) => void
}

/** 选场景：行业标签 + 场景卡网格（服务端分页），首张为「不指定场景」，选中后显示场景详情 */
export default function SceneStep({ industries, industry, onIndustry, sceneKey, onPick }: Props) {
  const text = useGenerationText()
  /* 场景列表与分页三态 */
  const [items, setItems] = useState<EcomScene[] | null>(null)
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)
  const moreCtrl = useRef<AbortController | null>(null)

  // 切换行业（或重试）：从第 1 页重新加载
  useEffect(() => {
    const controller = new AbortController()
    moreCtrl.current?.abort()
    setItems(null); setError(''); setPage(1); setLoadingMore(false)
    void listEcomScenes(industry, 1, PAGE_SIZE, controller.signal)
      .then((r) => { setItems(r.items); setTotal(r.total) })
      .catch((err) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : text('加载场景失败', 'Failed to load scenes')) })
    return () => controller.abort()
  }, [industry, reloadKey, text])

  // 加载更多：追加下一页
  async function loadMore() {
    const controller = new AbortController()
    moreCtrl.current = controller
    setLoadingMore(true); setError('')
    try {
      const r = await listEcomScenes(industry, page + 1, PAGE_SIZE, controller.signal)
      setItems((prev) => [...(prev || []), ...r.items.filter((s) => !(prev || []).some((p) => p.key === s.key))])
      setTotal(r.total); setPage(page + 1)
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : text('加载场景失败', 'Failed to load scenes'))
    } finally {
      if (!controller.signal.aborted) setLoadingMore(false)
    }
  }

  const selected = items?.find((s) => s.key === sceneKey) || null

  return <div className="pf-es-step">
    <div className="pf-tool-field"><span>{text('行业', 'Industry')}</span>
      <div className="pf-tool-chips">{industries.map((i) => <button key={i.id} type="button" className={industry === i.id ? 'active' : ''} onClick={() => onIndustry(i.id)}>{i.name}</button>)}</div>
    </div>
    {error && !items ? <div className="pf-es-actions">
      <p className="pf-tool-error">{error}</p>
      <Button variant="outline" size="sm" onClick={() => setReloadKey((k) => k + 1)}><RefreshCw size={14} />{text('重试', 'Retry')}</Button>
    </div> : !items ? <p className="pf-muted"><Loader2 size={16} className="pf-tool-spin" /> {text('加载场景…', 'Loading scenes…')}</p> : <>
      <div className="pf-es-grid">
        <button type="button" className={`pf-es-card pf-es-scene${!sceneKey ? ' active' : ''}`} aria-pressed={!sceneKey} onClick={() => onPick(null)}>
          <span className="pf-es-scene-cover pf-es-scene-none"><Ban size={26} /></span>
          <strong>{text('不指定场景', 'No scene')}</strong>
          <span className="pf-muted pf-es-scene-desc">{text('按品类默认画面', 'Category default look')}</span>
          {!sceneKey ? <span className="pf-es-tick"><Check size={14} /></span> : null}
        </button>
        {items.map((s) => <SceneCard key={s.key} scene={s} active={s.key === sceneKey} onPick={() => onPick({ key: s.key, name: s.name })} />)}
      </div>
      {!items.length ? <p className="pf-muted">{text('这个行业还没有场景，可以换个行业看看，或直接用「不指定场景」。', 'No scenes in this industry yet.')}</p> : null}
      {error ? <p className="pf-tool-error">{error}</p> : null}
      {items.length < total ? <div className="pf-es-actions">
        <Button variant="outline" size="sm" disabled={loadingMore} onClick={() => void loadMore()}>
          {loadingMore ? <Loader2 size={14} className="pf-tool-spin" /> : null}{text(`加载更多（${items.length}/${total}）`, `Load more (${items.length}/${total})`)}
        </Button>
      </div> : null}
      {selected ? <SceneDetail scene={selected} /> : sceneKey ? <p className="pf-muted">{text('已选上次用过的场景，可在上方重新选择。', 'Using your last scene.')}</p> : null}
    </>}
  </div>
}

/** 单张场景卡：封面（无封面时用背景色色块 + 场景名占位）、名称、一行描述 */
function SceneCard({ scene, active, onPick }: { scene: EcomScene; active: boolean; onPick: () => void }) {
  const cover = resolveToolMediaUrl(scene.cover_url)
  const color = /^[0-9a-f]{6}$/i.test(scene.spec?.bg_color || '') ? `#${scene.spec.bg_color}` : 'var(--pf-bg)'
  return <button type="button" className={`pf-es-card pf-es-scene${active ? ' active' : ''}`} aria-pressed={active} onClick={onPick}>
    {cover ? <img src={cover} alt="" loading="lazy" />
      : <span className="pf-es-scene-cover" style={{ background: color }}><span>{scene.name}</span></span>}
    <strong>{scene.name}</strong>
    {scene.description ? <span className="pf-muted pf-es-scene-desc">{scene.description}</span> : null}
    {active ? <span className="pf-es-tick"><Check size={14} /></span> : null}
  </button>
}

/** 所选场景详情：背景 / 台面 / 道具 / 光线 / 氛围，帮助用户确认 */
function SceneDetail({ scene }: { scene: EcomScene }) {
  const text = useGenerationText()
  const spec = scene.spec || ({} as Partial<EcomScene['spec']>)
  const rows: [string, string][] = [
    [text('背景', 'Backdrop'), [spec.backdrop, spec.palette].filter(Boolean).join(' · ')],
    [text('台面', 'Surface'), spec.surface || ''],
    [text('光线', 'Light'), spec.light || ''],
    [text('氛围', 'Mood'), spec.mood || ''],
  ]
  return <div className="pf-es-checklist pf-es-scene-detail">
    <strong>{text(`已选场景：${scene.name}`, `Scene: ${scene.name}`)}</strong>
    <dl>
      {rows.filter(([, v]) => v).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
      {spec.props?.length ? <div><dt>{text('道具', 'Props')}</dt><dd className="pf-tool-chips">{spec.props.map((p) => <span key={p} className="pf-badge">{p}</span>)}</dd></div> : null}
    </dl>
  </div>
}
