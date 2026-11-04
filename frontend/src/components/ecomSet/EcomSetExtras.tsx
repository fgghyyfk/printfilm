import { useEffect, useState } from 'react'
import { Loader2, Palette, Rows3 } from 'lucide-react'
import { downloadEcomSetLong, recolorEcomSet, type EcomSet } from '../../api/ecomSet'
import { getEcomProduct, type EcomProduct } from '../../api/ecomProducts'
import { canDownloadSet, defaultZipName } from '../../lib/ecomSet'
import { useGenerationText } from '../../lib/generationMedia'
import Button from '../ui/Button'

type Props = { set: EcomSet; onOpenSet: (id: number) => void; onError: (message: string) => void }

/** 套图结果的额外操作：下载详情长图；从商品库建的套图可「同款换色」（沿用方案，新建一套待生成） */
export default function EcomSetExtras({ set, onOpenSet, onError }: Props) {
  const text = useGenerationText()
  const productId = set.styled?.product_id || null
  /* 商品颜色款与操作状态 */
  const [product, setProduct] = useState<EcomProduct | null>(null)
  const [busy, setBusy] = useState('')

  useEffect(() => {
    if (!productId) return
    const controller = new AbortController()
    void getEcomProduct(productId, controller.signal).then(setProduct).catch(() => undefined)
    return () => controller.abort()
  }, [productId])

  // 下载长图
  async function downloadLong() {
    setBusy('long')
    try { await downloadEcomSetLong(set.id, defaultZipName(set).replace(/\.zip$/, '-详情长图.jpg')) }
    catch (err) { onError(err instanceof Error ? err.message : text('下载失败', 'Download failed')) }
    finally { setBusy('') }
  }

  // 换另一个颜色款
  async function recolor(index: number) {
    setBusy(`c${index}`)
    try { const next = await recolorEcomSet(set.id, index); onOpenSet(next.id) }
    catch (err) { onError(err instanceof Error ? err.message : text('换色失败', 'Failed')) }
    finally { setBusy('') }
  }

  const current = set.styled?.color_index ?? 0
  const others = (product?.colors || []).map((c, i) => ({ ...c, i })).filter((c) => c.i !== current)
  return <>
    {others.map((c) => <Button key={c.i} variant="text" size="sm" disabled={!!busy} onClick={() => void recolor(c.i)}>
      {busy === `c${c.i}` ? <Loader2 size={14} className="pf-tool-spin" /> : <Palette size={14} />}{text(`换成${c.name}`, `Make ${c.name}`)}
    </Button>)}
    <Button variant="outline" size="sm" disabled={!canDownloadSet(set) || !!busy} onClick={() => void downloadLong()}>
      {busy === 'long' ? <Loader2 size={14} className="pf-tool-spin" /> : <Rows3 size={14} />}{text('下载长图', 'Long image')}
    </Button>
  </>
}
