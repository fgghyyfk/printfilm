import { useEffect, useRef, useState, type PointerEvent } from 'react'
import { request, date } from './api'
import { PreviewContent } from './AssetPreview'

type Signed = { id: number; signer_name: string; created_at: string; template_version: string }
type Status = { template: { version: string; title: string; body: string }; current: Signed | null }
type Point = [number, number]

// 账号签署一次，服务端合成全文存档；每件视频仍需独立核验。
export default function AuthorizationPanel({ onSigned }: { onSigned?: () => void }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [letter, setLetter] = useState<{ url: string; filename: string } | null>(null)
  const [name, setName] = useState('')
  const [accepted, setAccepted] = useState(false)
  const canvas = useRef<HTMLCanvasElement>(null)
  const strokes = useRef<Point[][]>([])
  const drawing = useRef<number | null>(null)
  useEffect(() => { let active = true; request<Status>('/authorization').then(value => { if (active) setStatus(value) }).catch(e => { if (active) setError(e.message) }); return () => { active = false } }, [])
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
  async function sign() {
    if (!status) return
    const valid = strokes.current.filter(stroke => stroke.length >= 2)
    if (valid.flat().length < 12) { setError('请在签名区完整手写签名，再确认签署。'); return }
    setBusy(true); setError('')
    try {
      const result = await request<Signed>('/authorization', 'POST', { template_version: status.template.version, signer_name: name, accepted_terms: accepted, strokes: strokes.current.filter(stroke => stroke.length >= 2) })
      setStatus({ ...status, current: result }); onSigned?.()
    } catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }
  return <section className="pf-market-authorization"><h2>PrintFilm 作品授权函</h2><p>按现行条款签署一次，本账号后续 PrintFilm 作品可引用。无需另外填写创作记录或上传版权证明。</p>
    {status && <><details><summary>阅读完整授权函 · {status.template.version}</summary><h3>{status.template.title}</h3><div className="pf-market-letter">{status.template.body}</div></details>
      {status.current ? <><p className="pf-market-notice">已签署：{status.current.signer_name} · {date(status.current.created_at)}。可用于当前视频的发布审核。</p><button type="button" onClick={async () => { try { const value = await request<{ url: string; filename?: string }>(`/authorizations/${status.current!.id}/letter`); setLetter({ url: value.url, filename: value.filename || '授权函.pdf' }) } catch (e) { setError((e as Error).message) } }}>查看已签授权函</button>{letter && <PreviewContent key={letter.url} url={letter.url} filename={letter.filename} />}</> : <div className="pf-market-fields"><label>签署人真实姓名<input value={name} minLength={2} maxLength={80} onChange={e => setName(e.target.value)} disabled={busy} /></label><p>在下方手写签名（支持鼠标或触屏）</p><canvas aria-label="手写签名区域" ref={canvas} width={800} height={240} className="pf-market-signature" onPointerDown={e => { if (busy || drawing.current !== null || strokes.current.length >= 40) return; e.currentTarget.setPointerCapture(e.pointerId); drawing.current = e.pointerId; strokes.current.push([point(e)]) }} onPointerMove={move} onPointerUp={e => { if (drawing.current === e.pointerId) drawing.current = null }} onPointerCancel={() => { drawing.current = null }} /><button type="button" disabled={busy} onClick={() => { strokes.current = []; canvas.current?.getContext('2d')?.clearRect(0, 0, 800, 240) }}>清除签名</button><label><input type="checkbox" checked={accepted} disabled={busy} onChange={e => setAccepted(e.target.checked)} />我已阅读完整授权函，有权签署并确认以上声明；具体交易仍需另行订立合同。</label><button type="button" disabled={busy || !accepted || name.trim().length < 2} onClick={sign}>{busy ? '正在存档…' : '确认签署并保存授权函'}</button></div>}</>}
    {!status && !error && <p role="status">正在读取授权条款…</p>}{error && <p role="alert">{error}</p>}
  </section>
}

