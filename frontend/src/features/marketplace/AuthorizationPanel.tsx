import { useEffect, useRef, useState, type PointerEvent } from 'react'
import { date, letterFile, request } from './api'

type Signed = { id: number; signer_name: string; created_at: string; template_version: string }
type Status = { template: { version: string; title: string; notices: string[]; body: string }; current: Signed | null }
type Point = [number, number]
type Preview = { url: string; image: boolean }

/** 账号签署一次。条款在页面内阅读，已签署文件默认在线预览，点下载才保存。 */
export default function AuthorizationPanel({ onSigned }: { onSigned?: () => void }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [name, setName] = useState('')
  const [adult, setAdult] = useState(false)
  const [readAll, setReadAll] = useState(false)
  const [acceptKey, setAcceptKey] = useState(false)
  const canvas = useRef<HTMLCanvasElement>(null)
  const strokes = useRef<Point[][]>([])
  const drawing = useRef<number | null>(null)
  const ready = adult && readAll && acceptKey && name.trim().length >= 2
  useEffect(() => { let active = true; request<Status>('/authorization').then(value => { if (active) setStatus(value) }).catch(e => { if (active) setError(e.message) }); return () => { active = false } }, [])
  useEffect(() => {
    const id = status?.current?.id
    if (!id) return
    let active = true
    let objectUrl = ''
    letterFile(id).then(blob => {
      if (!active) return
      objectUrl = URL.createObjectURL(blob)
      setPreview({ url: objectUrl, image: blob.type.startsWith('image/') })
    }).catch(e => { if (active) setError((e as Error).message) })
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [status?.current?.id])
  function point(e: PointerEvent<HTMLCanvasElement>): Point {
    const rect = e.currentTarget.getBoundingClientRect()
    return [Math.max(0, Math.min(800, (e.clientX - rect.left) / rect.width * 800)), Math.max(0, Math.min(240, (e.clientY - rect.top) / rect.height * 240))]
  }
  function move(e: PointerEvent<HTMLCanvasElement>) {
    if (drawing.current !== e.pointerId) return
    const stroke = strokes.current[strokes.current.length - 1]
    if (stroke.length >= 1000) return
    const next = point(e), previous = stroke[stroke.length - 1]
    const ctx = canvas.current?.getContext('2d')
    if (ctx) { ctx.lineWidth = 3; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(...previous); ctx.lineTo(...next); ctx.stroke() }
    stroke.push(next)
  }
  function downloadLetter() {
    if (!preview) return
    const link = document.createElement('a')
    link.href = preview.url
    link.download = preview.image ? '授权函.png' : '授权函.pdf'
    link.click()
  }
  async function sign() {
    if (!status || !ready) return
    const valid = strokes.current.filter(stroke => stroke.length >= 2)
    if (valid.flat().length < 12) { setError('请在签名区完整手写签名，再确认签署。'); return }
    setBusy(true); setError('')
    try {
      const result = await request<Signed>('/authorization', 'POST', { template_version: status.template.version, signer_name: name.trim(), accepted_terms: true, adult: true, read_all: true, accept_key: true, strokes: valid })
      setStatus({ ...status, current: result }); onSigned?.()
    } catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  return <section className="pf-market-authorization"><h2>PrintFilm 作品授权函</h2><p>按现行条款签署一次，本账号后续 PrintFilm 作品可引用。本函是许可使用，不是著作权转让。</p>
    {status && <>
      <h3>{status.template.title}</h3>
      <p>版本 {status.template.version}</p>
      <ol className="pf-market-letter-notices">{status.template.notices.map(item => <li key={item}>{item}</li>)}</ol>
      <div className="pf-market-letter">{status.template.body}</div>
      {status.current ? <>
        <p className="pf-market-notice">已签署：{status.current.signer_name} · {date(status.current.created_at)}。下面是存档预览，不会自动下载。</p>
        <div className="pf-market-letter-actions"><button type="button" onClick={downloadLetter} disabled={!preview}>下载授权函</button></div>
        {preview && (preview.image ? <img className="pf-market-letter-image" src={preview.url} alt="已签署授权函" /> : <iframe className="pf-market-letter-frame" src={preview.url} title="授权函预览" />)}
      </> : <div className="pf-market-fields">
        <label>签署人真实姓名<input value={name} minLength={2} maxLength={80} onChange={e => setName(e.target.value)} disabled={busy} /></label>
        <p>在下方手写签名（支持鼠标或触屏）</p>
        <canvas aria-label="手写签名区域" ref={canvas} width={800} height={240} className="pf-market-signature" onPointerDown={e => { if (busy || drawing.current !== null || strokes.current.length >= 40) return; try { e.currentTarget.setPointerCapture(e.pointerId) } catch { /* 当前指针无法捕获时仍记录笔画 */ } drawing.current = e.pointerId; strokes.current.push([point(e)]) }} onPointerMove={move} onPointerUp={e => { if (drawing.current === e.pointerId) drawing.current = null }} onPointerCancel={() => { drawing.current = null }} />
        <button type="button" disabled={busy} onClick={() => { strokes.current = []; canvas.current?.getContext('2d')?.clearRect(0, 0, 800, 240) }}>清除签名</button>
        <label><input type="checkbox" checked={adult} disabled={busy} onChange={e => setAdult(e.target.checked)} />我已年满十八周岁，具有完全民事行为能力，所填姓名真实。</label>
        <label><input type="checkbox" checked={readAll} disabled={busy} onChange={e => setReadAll(e.target.checked)} />我已完整阅读本协议全文。</label>
        <label><input type="checkbox" checked={acceptKey} disabled={busy} onChange={e => setAcceptKey(e.target.checked)} />我已特别阅读并同意第7、8、10、11、13、15条。</label>
        <button type="button" disabled={busy || !ready} onClick={sign}>{busy ? '正在存档…' : '确认签署并保存授权函'}</button>
      </div>}
    </>}
    {!status && !error && <p role="status">正在读取授权条款…</p>}{error && <p role="alert">{error}</p>}
  </section>
}
