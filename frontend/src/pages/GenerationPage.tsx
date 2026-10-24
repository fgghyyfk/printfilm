import { useEffect, useRef, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'
import { ArrowRight, AudioLines, Check, Clock3, Download, ImagePlus, Layers, Loader2, Mic, Plus, RectangleHorizontal, RectangleVertical, Sparkles, Square, X } from 'lucide-react'
import { getGenerationConfig, listToolRuns, resolveToolMediaUrl, runStudioTool, type ToolRunRecord } from '../api/tools'
import BillingErrorNotice from '../components/billing/BillingErrorNotice'
import GenerationLayout from '../components/tools/GenerationLayout'
import GenerationSelect from '../components/tools/GenerationSelect'
import { mediaIcons, type MediaKind, useGenerationText } from '../lib/generationMedia'
import { fetchMediaBlob, triggerBlobDownload } from '../lib/clientDownload'
import { useMediaModelsCatalog } from '../hooks/useMediaModelsCatalog'
import { MediaCostNotice } from '../components/billing/MediaCostNotice'

const aliases: Record<string, MediaKind> = { video: 'video', t2v: 'video', v2v: 'video', image: 'image', t2i: 'image', i2i: 'image', i2p: 'image', ecom: 'image', audio: 'audio' }

export default function GenerationPage() {
  const { toolId } = useParams()
  if (!localStorage.getItem('token')) return <Navigate to={`/auth?next=${encodeURIComponent(`/tools/${toolId || 'video'}`)}`} replace />
  if (!toolId || aliases[toolId] !== toolId) return <Navigate to={`/tools/${aliases[toolId || 'video'] || 'video'}`} replace />
  return <GenerationWorkspace kind={aliases[toolId]} key={toolId} />
}

function GenerationWorkspace({ kind }: { kind: MediaKind }) {
  const text = useGenerationText()
  const Icon = mediaIcons[kind]
  const [prompt, setPrompt] = useState('')
  const [ratio, setRatio] = useState(kind === 'image' ? '1:1' : '9:16')
  const [duration, setDuration] = useState('5s')
  const [voice, setVoice] = useState('narrator_calm')
  const [files, setFiles] = useState<File[]>([])
  const [previews, setPreviews] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [recent, setRecent] = useState<ToolRunRecord[]>([])
  const [recentTotal, setRecentTotal] = useState(0)
  const [historyError, setHistoryError] = useState('')
  const [model, setModel] = useState('')
  const [resolution, setResolution] = useState(kind === 'image' ? '2K' : '480p')
  const catalog = useMediaModelsCatalog()
  const models = kind === 'image' ? catalog?.image_models ?? [] : catalog?.video_models ?? []
  const selectedModel = models.find(item => item.id === model)
  const allowedResolutions = selectedModel?.available_resolutions ?? selectedModel?.allowed_resolutions ?? []
  const invalidParameters = kind !== 'audio' && (!catalog || !selectedModel || selectedModel.available === false || !allowedResolutions.includes(resolution) || !selectedModel.allowed_aspect_ratios?.includes(ratio) || (kind === 'video' && (Number(duration.replace('s', '')) < (selectedModel.duration_min ?? 4) || Number(duration.replace('s', '')) > (selectedModel.duration_max ?? 30))))
  const [revision, setRevision] = useState(0)
  const [downloading, setDownloading] = useState<number | null>(null)
  const upload = useRef<HTMLInputElement>(null)
  const maxLength = kind === 'audio' ? 2000 : 7000
  useEffect(() => {
    let disposed = false
    getGenerationConfig().then(data => { if (!disposed && kind === 'audio') setModel(data.models[kind] || '') }).catch(() => {})
    return () => { disposed = true }
  }, [kind])
  useEffect(() => {
    if (catalog && !model && kind !== 'audio') setModel(kind === 'image' ? catalog.defaults.image_model : catalog.defaults.video_model)
  }, [catalog, model, kind])
  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    async function refresh() {
      try {
        const data = await listToolRuns(1, 6, { kind })
        if (disposed) return
        setRecent(data.items); setRecentTotal(data.total); setHistoryError('')
      } catch (err) { if (!disposed) setHistoryError(err instanceof Error ? err.message : text('加载失败', 'Loading failed')) }
      finally { if (!disposed) timer = setTimeout(refresh, 5000) }
    }
    void refresh()
    return () => { disposed = true; clearTimeout(timer) }
  }, [kind, revision, text])
  useEffect(() => {
    const urls = files.map(file => URL.createObjectURL(file))
    setPreviews(urls)
    return () => urls.forEach(url => URL.revokeObjectURL(url))
  }, [files])

  function pickFiles(incoming: File[]) {
    const limit = kind === 'video' ? 1 : selectedModel?.reference_limits?.images ?? 9
    if (incoming.some(file => file.size > 40 * 1024 * 1024)) { setError(text('单个文件不能超过 40MB', 'Each file must be under 40 MB')); return }
    if (files.length + incoming.length > limit) { setError(text(`最多选择 ${limit} 个参考文件`, `Choose up to ${limit} reference files`)); return }
    setFiles(previous => [...previous, ...incoming]); setError('')
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (submitting || !prompt.trim()) return
    setSubmitting(true); setError(''); setNotice('')
    try {
      const result = await runStudioTool({ toolId: kind === 'audio' ? 'audio' : kind === 'video' ? files.length ? 'v2v' : 't2v' : files.length ? 'i2i' : 't2i',
        prompt, files, ratio, duration, voice, modelId: model, resolution })
      setNotice(text(`任务 ${result.task_id} 已提交，离开页面后仍会继续生成。`, `Task ${result.task_id} queued. Generation continues after you leave.`))
      setRevision(value => value + 1)
    } catch (err) { setError(err instanceof Error ? err.message : text('提交失败', 'Submission failed')) }
    finally { setSubmitting(false) }
  }
  async function download(item: ToolRunRecord) {
    setDownloading(item.id); setError('')
    try {
      const url = resolveToolMediaUrl(item.urls[0])
      triggerBlobDownload(await fetchMediaBlob(url), `printfilm_${kind}_${item.id}.${kind === 'video' ? 'mp4' : kind === 'audio' ? 'mp3' : 'png'}`)
    } catch (err) { setError(err instanceof Error ? err.message : text('下载失败', 'Download failed')) }
    finally { setDownloading(null) }
  }
  const titles = {
    video: [text('一句话，', 'One idea.'), text('生成你的下一幕', 'Your next scene.')],
    image: [text('一句话，', 'One idea.'), text('画出你的想象', 'Bring it to life.')],
    audio: [text('一段文字，', 'Your words.'), text('让故事开口', 'A voice to tell them.')],
  }
  const placeholder = kind === 'audio' ? text('输入你想朗读的文字…', 'Enter the text you want to read…') : kind === 'image'
    ? text('描述你想生成的图片，或说明如何修改参考图。试试：午后阳光穿过玻璃，桌上一枝白色郁金香，胶片质感。', 'Describe an image or the changes to your references…')
    : text('描述你想生成的视频：主体 + 动作 + 环境 + 运镜。试试：一只松鼠抱着松果，金色秋日森林，镜头缓慢推进。', 'Describe your video: subject, action, setting, and camera movement…')

  return <GenerationLayout>
    <header className={`gen-hero gen-hero-${kind}`}>
      <div className="gen-eyebrow"><Icon size={22} />{text(kind === 'video' ? 'AI 视频' : kind === 'image' ? 'AI 图片' : 'AI 音频', `AI ${kind.toUpperCase()}`)}</div>
      <h1>{titles[kind][0]}<span>{titles[kind][1]}</span></h1>
      <p>{kind === 'audio' ? text('选一款音色，为你的文字注入情绪与温度。', 'Choose a voice and give your words warmth.') : text('从一个想法开始，让灵感自然发生。', 'Start with an idea. Let inspiration happen.')}</p>
      <div className="gen-hero-art" aria-hidden>{kind === 'audio' ? <><AudioLines className="gen-wave" /><Mic /></> : <Icon />}</div>
    </header>
    <form className="gen-composer" onSubmit={submit}>
      {kind !== 'audio' && <div className="gen-price-summary">
        <MediaCostNotice modelId={model} resolution={resolution} seconds={kind === 'video' ? Number(duration.replace('s', '')) : undefined} />
        {kind === 'video' && files.length === 0 && <span>另含首帧图片：<MediaCostNotice modelId={catalog?.defaults.image_model} resolution="2K" /></span>}
        {invalidParameters && <p role="alert">当前模型不支持所选参数，请重新选择分辨率、画幅或时长。</p>}
      </div>}
      <div className="gen-composer-heading"><span><Sparkles size={15} />{text(kind === 'audio' ? '朗读内容' : '创作描述', kind === 'audio' ? 'Your script' : 'Your creative brief')}</span><small>{text('把想象，写成下一件作品', 'A little detail goes a long way')}</small></div>
      <div className="gen-input-area">
        {kind !== 'audio' && <div className={`gen-references ${files.length ? 'has-files' : ''}`}>
          <span className="gen-ref-count"><ImagePlus size={14} />{files.length}/{kind === 'video' ? 1 : selectedModel?.reference_limits?.images ?? 9}</span>
          <div className="gen-ref-grid">
            {previews.map((url, index) => <div className="gen-ref-item" key={url}>
              {files[index]?.type.startsWith('video/') ? <video src={url} muted /> : <img src={url} alt={text('参考图', 'Reference')} />}
              <button type="button" aria-label={text('移除参考文件', 'Remove reference')} onClick={() => setFiles(items => items.filter((_, i) => i !== index))}><X size={13} /></button>
            </div>)}
            {files.length < (kind === 'video' ? 1 : selectedModel?.reference_limits?.images ?? 9) && <button type="button" className="gen-upload" onClick={() => upload.current?.click()}
              onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); pickFiles(Array.from(event.dataTransfer.files)) }}>
              <Plus size={24} /><span>{text(kind === 'video' ? '添加首帧 / 视频' : '选择参考图', kind === 'video' ? 'Add image / video' : 'Add reference')}</span>
            </button>}
          </div>
          <input type="file" hidden ref={upload} multiple={kind === 'image'} accept={kind === 'video' ? 'image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm' : 'image/png,image/jpeg,image/webp'} onChange={event => { pickFiles(Array.from(event.target.files || [])); event.target.value = '' }} />
        </div>}
        <div className="gen-prompt-wrap">
          <textarea aria-label={text('生成内容', 'Generation prompt')} placeholder={placeholder} maxLength={maxLength} value={prompt} onChange={event => { setPrompt(event.target.value); setNotice('') }} />
          <span className="gen-counter">{prompt.length.toLocaleString()} / {maxLength.toLocaleString()}{kind === 'audio' ? text(` · 约 ${Math.ceil(prompt.length / 4)} 秒`, ` · ~${Math.ceil(prompt.length / 4)} sec`) : ''}</span>
        </div>
      </div>
      <div className="gen-toolbar">
        {kind === 'audio' ? <span className="gen-control gen-model">{model || text('站点默认模型', 'Default model')}</span> : <GenerationSelect label={text('生成模型', 'Model')} icon={<Icon size={19} />} value={model} onChange={setModel} options={models.filter(item => item.available !== false).map(item => ({ value: item.id, label: item.label, description: item.pricing_hint }))} />}
        {kind === 'audio' ? <GenerationSelect label={text('音色', 'Voice')} icon={<Mic size={18} />} value={voice} onChange={setVoice} options={[
          { value: 'narrator_calm', label: text('沉静旁白', 'Calm narrator'), description: text('平稳自然，适合故事旁白', 'Steady and natural, for narration') },
          { value: 'warm_storyteller', label: text('温暖讲述', 'Warm storyteller'), description: text('柔和亲切，适合温馨叙事', 'Warm and gentle, for storytelling') },
          { value: 'teacher_clear', label: text('清晰男声', 'Clear male voice'), description: text('清楚明快，适合知识讲解', 'Clear and bright, for explainers') },
        ]} /> : <>
          <GenerationSelect label={text('分辨率', 'Resolution')} value={resolution} onChange={setResolution} options={allowedResolutions.map(value => ({ value, label: value, description: selectedModel?.prices?.[value] ? `¥${selectedModel.prices[value]}/${kind === 'video' ? '秒' : '张'}` : '' }))} />
          <GenerationSelect label={text('画幅', 'Aspect ratio')} value={ratio} onChange={setRatio} options={(selectedModel?.allowed_aspect_ratios ?? []).map(value => ({ value, label: value, icon: value === '1:1' ? <Square size={19} /> : value === '9:16' || value === '3:4' ? <RectangleVertical size={19} /> : <RectangleHorizontal size={21} /> }))} />
          {kind === 'video' && <GenerationSelect label={text('视频时长', 'Duration')} icon={<Clock3 size={18} />} value={duration} onChange={setDuration} options={[4, 5, 8, 10, 15, 20, 25, 30].filter(value => value >= (selectedModel?.duration_min ?? 4) && value <= (selectedModel?.duration_max ?? 30)).map(value => ({ value: `${value}s`, label: `${value} 秒`, description: `${resolution} · 单次生成一个片段` }))} />}
          <span className="gen-control gen-output-count"><Layers size={17} /><span><small>{text('生成数量', 'Output')}</small><strong>× 1</strong></span></span>
        </>}
        <div className="gen-submit-group"><span>{text('生成记录自动保存', 'Creations saved automatically')}</span>
          <button className="gen-primary" disabled={submitting || !prompt.trim() || invalidParameters} type="submit">{submitting ? <Loader2 className="gen-spin" size={18} /> : <Icon size={18} />}{text(submitting ? '提交中' : kind === 'audio' ? '生成语音' : '创作', submitting ? 'Submitting' : 'Generate')}</button>
        </div>
      </div>
    </form>
    {error && <BillingErrorNotice message={error} />}
    {notice && <div className="gen-notice" role="status"><Check size={18} />{notice}<Link to={`/tasks?kind=${kind}`}>{text('查看任务', 'View task')}<ArrowRight size={15} /></Link></div>}
    <section className="gen-recent">
      <header><h2>{text('最近生成', 'Recent creations')}</h2><Link to={`/tasks?kind=${kind}`}>{text(`查看全部 ${recentTotal} 个`, `View all ${recentTotal}`)}<ArrowRight size={17} /></Link></header>
      {historyError && <p className="gen-inline-error" role="alert">{historyError}</p>}
      {!recent.length && !historyError ? <div className="gen-empty"><Icon size={34} /><strong>{text('你的灵感，从这里开始', 'Your next idea starts here')}</strong><p>{text('提交第一个生成任务，作品会自动出现在这里。', 'Submit your first generation. Your creations will appear here.')}</p></div> : <div className="gen-gallery">
        {recent.map(item => <article key={item.id} className="gen-gallery-item">
          <Link to={`/tasks?kind=${kind}&task_id=${encodeURIComponent(item.task_id || String(item.id))}`} className="gen-gallery-preview" aria-label={item.prompt}>
            {kind === 'image' && (item.preview_url || item.urls[0]) ? <img src={resolveToolMediaUrl(item.preview_url || item.urls[0])} alt={item.prompt} /> : kind === 'video' && item.urls[0] ? <video src={resolveToolMediaUrl(item.urls[0])} poster={resolveToolMediaUrl(item.preview_url)} muted preload="metadata" /> : kind === 'video' && item.preview_url ? <img src={resolveToolMediaUrl(item.preview_url)} alt={item.prompt} /> : <Icon size={38} />}
            <span className={`gen-result-status is-${item.status}`}>{text(item.status === 'succeeded' ? '已完成' : item.status === 'failed' ? '失败' : item.status === 'cancelled' ? '已取消' : '处理中', item.status)}</span>
          </Link>
          {kind === 'audio' && item.urls[0] && <audio controls src={resolveToolMediaUrl(item.urls[0])} preload="metadata" />}
          <div className="gen-gallery-caption"><p title={item.prompt}>{item.prompt}</p>{item.status === 'succeeded' && item.urls[0] && <button type="button" className="gen-icon-button" disabled={downloading === item.id} onClick={() => void download(item)} aria-label={text('下载', 'Download')}><Download size={16} /></button>}</div>
        </article>)}
      </div>}
    </section>
  </GenerationLayout>
}
