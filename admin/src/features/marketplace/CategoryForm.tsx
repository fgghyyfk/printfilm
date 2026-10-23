import { useEffect, useState, type FormEvent } from 'react'
import { request } from './api'

type Config = { version: number; names: string[] }

export default function CategoryForm() {
  const [config, setConfig] = useState<Config | null>(null)
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true; setError(''); setConfig(null)
    request<Config>('/api/admin/marketplace/categories').then(value => {
      if (active) { setConfig(value); setText(value.names.join('\n')) }
    }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [revision])
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!config) return
    const names = text.split(/\r?\n/).map(name => name.trim()).filter(Boolean)
    if (!names.length || names.length > 30 || new Set(names).size !== names.length || names.some(name => name.length > 60)) {
      setError('请填写1至30个不重复的分类，每个名称最多60字。'); return
    }
    setBusy(true); setError(''); setMessage('')
    try {
      const value = await request<Config>('/api/admin/marketplace/categories', 'PUT', { version: config.version, names })
      setConfig(value); setText(value.names.join('\n')); setMessage('分类已保存，用户刷新广场及发布页后生效。')
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return <form className="market-ops-support" onSubmit={save}><h2>作品分类配置</h2><p>发布页分类选项与作品广场分类标签共用此配置。每行一个分类，按行的顺序展示。</p><label>分类名称<textarea required rows={10} maxLength={1830} value={text} disabled={!config || busy} onChange={e => setText(e.target.value)} placeholder="短片&#10;广告&#10;动画" /></label><p>新增一行即可添加，调整行顺序即可排序。移除或改名仅影响后续选择与分类入口，历史作品分类不自动改写；编辑历史作品时可保留原分类。</p><div className="flex gap-3"><button type="submit" disabled={!config || busy}>{busy ? '保存中…' : '保存分类'}</button><button type="button" disabled={busy} onClick={() => { if (window.confirm('重新加载将丢弃未保存修改，继续吗？')) { setMessage(''); setRevision(v => v + 1) } }}>重新加载</button></div>{error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}</form>
}
