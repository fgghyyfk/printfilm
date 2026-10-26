import { useEffect, useState } from 'react'
import { labels, media, request, type Row } from './api'

// 浏览器原生预览；不把文件或签名链接保存到 localStorage。
export function PreviewContent({ url, filename, video = false, onReady, onFailure }: { url: string; filename: string; video?: boolean; onReady?: () => void; onFailure?: () => void }) {
  const [failed, setFailed] = useState(false)
  function fail() { setFailed(true); onFailure?.() }
  const pdf = /\.pdf$/i.test(filename)
  return <div className="pf-market-file-preview">
    {video ? <video src={url} controls preload="metadata" onLoadedMetadata={onReady} onError={fail} /> : pdf ? <><iframe src={url} title={`PDF预览：${filename}`} onLoad={onReady} /><a href={url} target="_blank" rel="noreferrer">在新窗口查看 PDF</a></> : <img src={url} alt={filename} onLoad={onReady} onError={fail} />}
    {failed && <p role="alert">{video ? '视频加载失败，请刷新链接重试；也可能是浏览器不支持该视频编码。' : '文件加载失败，请刷新链接重试，或检查文件服务的访问配置。'}</p>}
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
  return <div className="pf-market-asset-preview"><p>{asset?.filename || `文件 #${id}`} · {labels[asset?.status || ''] || '正在读取'}</p>
    {url && <div hidden={hideMedia}><PreviewContent key={url} url={url} filename={asset?.filename || ''} video={asset?.kind === 'video'} onReady={() => { onReady?.(id); onMediaReady?.(id) }} onFailure={() => { onMediaReady?.(id); if (hideMedia) setError('云端预览加载失败，已保留本地预览，可刷新预览链接重试。') }} /></div>}
    {asset?.status === 'processing' && <p>文件已上传，正在生成兼容浏览器的预览，请稍后刷新。</p>}
    {asset?.error && <p role="alert">{asset.error}</p>}{error && <p role="alert">{error}</p>}
    <button type="button" onClick={() => setRevision(v => v + 1)}>刷新预览链接</button>
  </div>
}
