import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import { Button } from '@/components/ui/button'

type PriceData = { active_version: number | null; enabled: boolean; prices: Record<string, Record<string, string>>;
  strategies: Array<{ id: string; capability: string; allowed_resolutions: string[]; duration_min?: number; duration_max?: number }> }

export function MediaPriceSettings() {
  const [data, setData] = useState<PriceData | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  async function load() {
    try { setData(await api<PriceData>('/api/admin/settings/billing/media-prices')); setError('') }
    catch (cause) { setError(cause instanceof Error ? cause.message : '价格加载失败') }
  }
  useEffect(() => { void load() }, [])
  async function toggle() {
    if (!data?.active_version) return
    setBusy(true)
    try {
      await api('/api/admin/settings/billing/media-prices/activation', { method: 'PATCH', body: JSON.stringify({ enabled: !data.enabled, expected_version: data.active_version }) })
      await load(); setNotice('计费模式已切换，仅影响新任务，在途任务保持原规则。')
    } catch (cause) { setError(cause instanceof Error ? cause.message : '切换失败') }
    finally { setBusy(false) }
  }
  async function publish() {
    if (!data) return
    setBusy(true); setError(''); setNotice('')
    try {
      const prices = Object.fromEntries(Object.entries(data.prices).map(([model, rows]) =>
        [model, Object.fromEntries(Object.entries(rows).filter(([, value]) => value.trim() !== ''))]))
      await api('/api/admin/settings/billing/media-prices', { method: 'POST', body: JSON.stringify({ prices, expected_version: data.active_version }) })
      await load(); setNotice('价格已发布，新任务立即生效，在途任务保持原价格。')
    } catch (cause) { setError(cause instanceof Error ? cause.message : '发布失败') }
    finally { setBusy(false) }
  }
  return <section className="settings-panel">
    <h3>图片与视频价格</h3>
    <p className="text-sm text-[var(--admin-muted)]">{data?.active_version ? `价格版本 #${data.active_version} · ${data.enabled ? '已启用' : '已停用，新任务沿用原计费'}` : '尚未发布：下方为现有规则换算的价格草稿。'} 图片按张、视频按提交秒数收费；空白档位不可生成。失败不自动切换路由。</p>
    {error && <p role="alert" className="text-red-600">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {data && ['image', 'video'].map(kind => <div key={kind} className="admin-table-wrap mt-4">
      <h4>{kind === 'image' ? '图片 · 元/张' : '视频 · 元/秒'}</h4>
      <table><thead><tr><th>模型</th><th>支持参数</th><th>分辨率与单价</th></tr></thead>
        <tbody>{data.strategies.filter(strategy => strategy.capability === kind).map(strategy => <tr key={strategy.id}>
          <td>{strategy.id}</td><td>{strategy.duration_max ? `${strategy.duration_min}–${strategy.duration_max} 秒` : '按成功交付张数'}</td>
          <td><div className="flex flex-wrap gap-3">{strategy.allowed_resolutions.map(resolution => <label key={resolution} className="flex items-center gap-2 text-sm">
            {resolution}<input type="number" min="0.0001" max="10000" step="0.0001" aria-label={`${strategy.id} ${resolution} 单价`} placeholder="未配置" className="w-28 rounded border p-2"
              value={data.prices[strategy.id]?.[resolution] ?? ''} onChange={event => setData({ ...data, prices: { ...data.prices, [strategy.id]: { ...data.prices[strategy.id], [resolution]: event.target.value } } })} />
          </label>)}</div></td>
        </tr>)}</tbody></table>
    </div>)}
    <div className="mt-4 flex gap-3"><Button type="button" disabled={!data || busy} onClick={() => void publish()}>{busy ? '发布中…' : '发布价格版本'}</Button><Button type="button" variant="outline" disabled={busy} onClick={() => void load()}>重新加载</Button>{data?.active_version && <Button type="button" variant="outline" disabled={busy} onClick={() => void toggle()}>{data.enabled ? '停用新任务固定计费' : '启用固定计费'}</Button>}</div>
  </section>
}
