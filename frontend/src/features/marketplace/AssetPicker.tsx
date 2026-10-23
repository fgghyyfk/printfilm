import { useEffect, useRef, useState } from 'react'
import { request, upload } from './api'
import { CheckCircle2, UploadCloud } from 'lucide-react'
import AssetPreview, { PreviewContent } from './AssetPreview'

// 上传状态与“处理就绪”分开显示，只允许绑定 ready 文件。
export default function AssetPicker({ kind, value, onChange, title = '选择或上传文件', required = false }: { kind: 'video' | 'document' | 'image'; value: number | null; onChange: (id: number) => void; title?: string; required?: boolean }) {
  /* 仅上传并绑定当前文件，不加载历史文件列表。 */


  const [error, setError] = useState('')
  const [progress, setProgress] = useState<number | null>(null)

  const [pending, setPending] = useState<number | null>(null)
  const controller = useRef<AbortController | null>(null)
  const [localFile, setLocalFile] = useState<File | null>(null)
  const [localAssetId, setLocalAssetId] = useState<number | null>(null)
  const [localUrl, setLocalUrl] = useState('')
  useEffect(() => {
    if (!localFile) { setLocalUrl(''); return }
    const url = URL.createObjectURL(localFile); setLocalUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [localFile])
  useEffect(() => {
    if (!pending) return
    let active = true
    // 独立查询处理状态，离开页面即清理轮询。
    const check = async () => {
      try {
        const asset = await request<{ status: string; error: string }>(`/assets/${pending}`)
        if (!active) return
        if (asset.status === 'ready') { onChange(pending); setPending(null); setProgress(null) }
        if (asset.status === 'failed') { setError(asset.error); setPending(null); setProgress(null) }
      } catch (e) { if (active) { setError((e as Error).message); setPending(null); setProgress(null) } }
    }
    const interval = window.setInterval(check, 5000); void check()
    return () => { active = false; window.clearInterval(interval) }
  }, [pending, onChange])
  // 续传失败保留会话，重新选择同一文件即可继续。
  async function choose(file?: File) {
    if (!file) return
    const allowed = kind === 'video' ? /\.(mp4|mov|webm)$/i : kind === 'image' ? /\.(png|jpe?g|webp)$/i : /\.(pdf|png|jpe?g|webp)$/i
    if (!allowed.test(file.name) || file.size > (kind === 'video' ? 500 : 20) * 1024 * 1024 || file.size === 0) { setError('文件类型或大小不符合要求'); return }
    setLocalFile(file); setLocalAssetId(null)
    setError(''); setProgress(0)
    controller.current = new AbortController()
    try { const id = await upload(file, kind, setProgress, controller.current.signal); setLocalAssetId(id); onChange(id); setPending(id) }
    catch (e) { setError((e as Error).message); setProgress(null) }
    finally { controller.current = null }
  }
  return <fieldset><legend>{title}{required && <span className="pf-req-mark">必填</span>}</legend>
    <label className="pf-market-upload-zone"><UploadCloud size={28} strokeWidth={1.4} /><strong>点击选择{kind === 'video' ? '视频' : kind === 'image' ? '图片' : '文件'}</strong><span>{kind === 'video' ? 'MP4 / MOV / WebM · 最大500MB · 最长30分钟' : kind === 'image' ? 'PNG / JPG / WebP · 最大20MB' : 'PDF / PNG / JPG / WebP · 最大20MB'}</span><input aria-label={`上传${title}`} type="file" disabled={progress !== null} accept={kind === 'video' ? '.mp4,.mov,.webm' : kind === 'image' ? '.png,.jpg,.jpeg,.webp' : '.pdf,.png,.jpg,.jpeg,.webp'} onChange={e => { void choose(e.target.files?.[0]); e.target.value = '' }} /></label>
    {progress !== null && <div className="pf-market-upload-progress" role="status"><p>{pending ? '上传完成，正在处理媒体。可稍后到我的文件查看。' : `上传 ${progress}%`}</p><progress value={progress} max={100} aria-label="上传进度" /></div>}
    {progress !== null && !pending && <button type="button" onClick={() => controller.current?.abort()}>取消上传</button>}
    {localFile && localUrl && <div><p>本地预览：{localFile.name} · {(localFile.size / 1024 / 1024).toFixed(2)} MB</p><p>仅供检查所选文件，不代表已上传或审核通过。</p><PreviewContent key={localUrl} url={localUrl} filename={localFile.name} video={kind === 'video'} /><button type="button" onClick={() => setLocalFile(null)}>关闭本地预览</button></div>}
    {value && <><p className="pf-market-upload-selection"><CheckCircle2 size={14} /> 已绑定文件 #{value}（新文件处理就绪后才会替换）</p><AssetPreview key={value} id={value} hideMedia={Boolean(localFile)} onReady={id => { if (id === localAssetId) { setLocalFile(null); setLocalAssetId(null) } }} /></>}{error && <p role="alert">{error}</p>}</fieldset>
}

