import { useEffect, useState } from 'react'
import { media, request, type Row } from './api'
import { explainMarketplaceError } from './messages'

const fileStatusNames: Record<string, string> = { ready: '上传完成', uploading: '上传中', processing: '正在校验文件', failed: '文件校验失败', cancelled: '已取消上传', expired: '上传已过期' }

// 浏览器原生预览；不把文件或签名链接保存到 localStorage。
export function PreviewContent({ url, filename, video = false, onReady, onFailure }: { url: string; filename: string; video?: boolean; onReady?: () => void; onFailure?: () => void }) {
  const [failed, setFailed] = useState(false)
  function fail() { setFailed(true); onFailure?.() }
  const pdf = /\.pdf$/i.test(filename)
  return <div className="pf-market-file-preview">
    {video ? <video src={url} controls preload="metadata" onLoadedMetadata={onReady} onError={fail} /> : pdf ? <><iframe src={url} title={`PDF预览：${filename}`} onLoad={onReady} /><a href={url} target="_blank" rel="noreferrer">在新窗口查看 PDF</a></> : <img src={url} alt={filename} onLoad={onReady} onError={fail} />}
    {failed && <p role="alert">{video ? '视频暂时无法预览，请重新加载；若仍无法播放，可能是浏览器不支持该视频编码。' : '文件暂时无法预览，请重新加载；若仍失败，请稍后重试或联系客服。'}</p>}
  </div>
}

export default function AssetPreview({ id, onReady, onMediaReady, hideMedia = false }: { id: number; onReady?: (id: number) => void; onMediaReady?: (id: number) => void; hideMedia?: boolean }) {
  const [asset, setAsset] = useState<Row | null>(null)
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true; setUrl(''); setError(''); setAsset(null)
    async function load() {
      try {
        const item = await request<Row>(`/assets/${id}`)
        if (!active) return
        setAsset(item)
        if (item.status === 'ready') {
          const link = await media('asset', id, 'original')
          if (active) {
            setUrl(link)
            // A signed cloud URL means the uploaded asset is ready to replace
            // the local object URL.  Notify the picker immediately so the
            // temporary local preview closes without another manual click.
            onReady?.(id)
          }
        } else if (item.status === 'failed') onMediaReady?.(id)
      } catch (e) { if (active) setError((e as Error).message) }
    }
    void load()
    return () => { active = false }
  }, [id, revision])
  useEffect(() => {
    if (!asset || !['uploading', 'processing'].includes(asset.status || '')) return
    const timer = window.setTimeout(() => setRevision(value => value + 1), 2000)
    return () => window.clearTimeout(timer)
  }, [asset])
  return <div className="pf-market-asset-preview"><p>{asset?.filename || '正在读取文件信息'} · {fileStatusNames[asset?.status || ''] || '正在读取状态'}</p>
    {url && <div hidden={hideMedia}><PreviewContent key={url} url={url} filename={asset?.filename || ''} video={asset?.kind === 'video'} onReady={() => { onReady?.(id); onMediaReady?.(id) }} onFailure={() => { onMediaReady?.(id); if (hideMedia) setError('上传文件暂时无法预览，请点击“重新加载预览”重试。') }} /></div>}
    {asset?.status === 'processing' && <p>文件已上传，正在校验文件信息，状态会自动更新。</p>}
    {asset?.error && <p role="alert">{explainMarketplaceError(asset.error)}</p>}{error && <p role="alert">{error}</p>}
    <button type="button" onClick={() => setRevision(v => v + 1)}>重新加载预览</button>
  </div>
}
