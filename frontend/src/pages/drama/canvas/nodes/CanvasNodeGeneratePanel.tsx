/** 选中节点底部：AI 提示词浮动面板（生图 / 生视频） */
import { useEffect, useMemo, useRef, useState, type FormEvent, type MouseEvent } from 'react'
import { ArrowUp, CircleHelp, Loader2, Sparkles, Wand2 } from 'lucide-react'
import { SeedanceRulesModal } from '../../../../components/drama/SeedanceRulesModal'
import { useCanvasStore } from '../CanvasStore'
import { CANVAS_GENERATABLE_KINDS, type CanvasNodeKind } from '../canvasTypes'
import {
  readImageGenerationOptions,
  type ImageGenerationOptions,
} from '../../../../lib/dramaGenerationOptions'
import {
  DEFAULT_VIDEO_GENERATION_OPTIONS,
  readVideoGenerationOptions,
  type VideoGenerationOptions,
} from '../../../../lib/dramaVideoGenerationOptions'
import { optimizePromptWithSkills } from '../../../../api/agentSkills'
import { useAgentSkillSelection } from '../../../../hooks/useAgentSkillSelection'
import { ensureStructureInPrompt } from '../../../../lib/dramaStructurePrompt'
import { DramaImageGenOptionsBar } from './DramaImageGenOptionsBar'
import { DramaSkillOptionsBar } from './DramaSkillOptionsBar'
import { DramaVideoGenOptionsBar } from './DramaVideoGenOptionsBar'
import { CanvasPromptEditor } from './CanvasPromptEditor'

type CanvasNodeGeneratePanelProps = {
  nodeId: string
  kind: CanvasNodeKind
  generating?: boolean
  defaultPrompt?: string
  /** 节点展示名，用于过滤弱占位提示词 */
  label?: string
  /** 是否已有参考图（资产库/上传） */
  hasMedia?: boolean
  /** 视频节点已保存的 Seedance 参数 */
  videoOptions?: Record<string, unknown>
  /** 生图节点已保存的模型/比例/清晰度 */
  imageOptions?: Record<string, unknown>
}

/** 按节点类型返回面板文案 */
function panelCopy(kind: CanvasNodeKind, hasMedia: boolean) {
  if (kind === 'video') {
    return {
      title: hasMedia ? '编辑并重生视频' : 'AI 生成视频',
      placeholder: '描述视频画面、镜头运动与氛围；键入 @ 引用已连线节点…',
      hint: 'Enter 生成视频 · @ 仅可引用已连线节点 · Shift+Enter 换行',
    }
  }
  if (kind === 'character') {
    return {
      title: hasMedia ? '编辑并重生角色' : 'AI 生角色',
      placeholder: '描述角色外貌、服饰与气质…',
      hint: 'Enter 生成 · Shift+Enter 换行',
    }
  }
  if (kind === 'scene') {
    return {
      title: hasMedia ? '编辑并重生场景' : 'AI 生场景',
      placeholder: '描述场景环境、光线与氛围…',
      hint: 'Enter 生成 · Shift+Enter 换行',
    }
  }
  return {
    title: hasMedia ? '编辑并重生图片' : 'AI 生图',
    placeholder: '描述画面内容；键入 @ 引用已连线节点…',
    hint: 'Enter 生成 · @ 仅可引用已连线节点 · Shift+Enter 换行',
  }
}

const PLACEHOLDER_PROMPT = /^(character|scene|prop|material|none|image|audio|video)\s+\S+$/i

/** 清洗默认提示词：只去掉「video 新视频」这类占位，保留用户短描述与 @ 引用 */
function sanitizePrompt(raw: string, kind: CanvasNodeKind, label: string): string {
  const text = (raw || '').trim()
  if (!text) return ''
  if (PLACEHOLDER_PROMPT.test(text)) return ''
  if (label && (text === `${kind} ${label}` || text === label)) return ''
  return text
}

/** 渲染 AI 提示词编辑面板 */
export function CanvasNodeGeneratePanel({
  nodeId,
  kind,
  generating = false,
  defaultPrompt = '',
  label = '',
  hasMedia = false,
  videoOptions: savedVideoOptions,
  imageOptions: savedImageOptions,
}: CanvasNodeGeneratePanelProps) {
  const {
    projectId,
    generateNodeImage,
    generateNodeVideo,
    setErrorMessage,
    projectImageStyleId,
    updateNodePrompt,
    updateNodeVideoOptions,
    updateNodeImageOptions,
    mentionableNodes,
    edges,
  } = useCanvasStore()
  const [prompt, setPrompt] = useState(() => sanitizePrompt(defaultPrompt, kind, label))
  /*
   * busy 正在提交生成
   * rulesOpen Seedance 规则弹窗
   * optimizing Skill 改写进行中
   */
  const [busy, setBusy] = useState(false)
  const [rulesOpen, setRulesOpen] = useState(false)
  const [optimizing, setOptimizing] = useState(false)
  const { skills, selectedIds, toggleSkill, selectAll, selectNone, uploadSkill, uploading, uploadError } =
    useAgentSkillSelection()
  // imageOptions 生图风格/模型/画幅（优先恢复节点上已保存选择）
  const [imageOptions, setImageOptions] = useState<ImageGenerationOptions>(() => {
    const saved = readImageGenerationOptions(savedImageOptions, kind)
    return {
      ...saved,
      image_style_id: saved.image_style_id || projectImageStyleId || undefined,
    }
  })
  // videoOpts Seedance 时长/比例/清晰度
  const [videoOpts, setVideoOpts] = useState<VideoGenerationOptions>(() => {
    const saved = readVideoGenerationOptions(savedVideoOptions)
    return {
      ...DEFAULT_VIDEO_GENERATION_OPTIONS,
      ...saved,
      image_style_id: saved.image_style_id || projectImageStyleId || undefined,
    }
  })
  const copy = panelCopy(kind, hasMedia)
  const allowMention = kind === 'video' || kind === 'image'
  const isVideo = kind === 'video'

  /* 可引用：仅已连线邻居（双向），并排除自身 */
  const mentionItems = useMemo(() => {
    const neighborIds = new Set<string>()
    for (const edge of edges) {
      if (edge.source === nodeId) neighborIds.add(edge.target)
      if (edge.target === nodeId) neighborIds.add(edge.source)
    }
    return mentionableNodes.filter(
      (n) => n.nodeId !== nodeId && neighborIds.has(n.nodeId),
    )
  }, [edges, mentionableNodes, nodeId])

  const lastNodeIdRef = useRef(nodeId)

  /* 换节点时强制同步；同一节点不要用空默认值清掉用户正文 */
  useEffect(() => {
    const switched = lastNodeIdRef.current !== nodeId
    lastNodeIdRef.current = nodeId
    const next = sanitizePrompt(defaultPrompt, kind, label)
    if (switched) {
      setPrompt(next)
      return
    }
    setPrompt((prev) => next || prev)
  }, [defaultPrompt, nodeId, kind, label])

  /* 仅切换节点时从节点恢复生图选项；编辑过程不覆盖本地选择 */
  useEffect(() => {
    const saved = readImageGenerationOptions(savedImageOptions, kind)
    setImageOptions({
      ...saved,
      image_style_id: saved.image_style_id || projectImageStyleId || undefined,
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 只在换节点时恢复
  }, [nodeId, kind])

  useEffect(() => {
    const saved = readVideoGenerationOptions(savedVideoOptions)
    setVideoOpts({
      ...DEFAULT_VIDEO_GENERATION_OPTIONS,
      ...saved,
      image_style_id: saved.image_style_id || projectImageStyleId || undefined,
    })
    // 仅切换节点时恢复已保存参数，避免编辑选项时被回写覆盖
  }, [nodeId])

  const isBusy = busy || generating || optimizing
  const canSubmit = prompt.trim().length > 0 && !isBusy
  const canOptimize = prompt.trim().length > 0 && selectedIds.length > 0 && !isBusy

  if (!CANVAS_GENERATABLE_KINDS.has(kind)) return null

  const stopFlowEvent = (event: MouseEvent) => {
    event.stopPropagation()
  }

  const submit = async () => {
    if (!canSubmit) return
    setBusy(true)
    try {
      // 提示词以框内为准（抽取/生成提示词时已含三视图等结构）；生图不再动态拼接
      const finalPrompt = prompt.trim()
      updateNodePrompt(nodeId, finalPrompt)
      if (isVideo) {
        updateNodeVideoOptions(nodeId, videoOpts)
        await generateNodeVideo(nodeId, finalPrompt, videoOpts)
      } else {
        updateNodeImageOptions(nodeId, imageOptions)
        await generateNodeImage(nodeId, finalPrompt, imageOptions)
      }
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : '生成失败')
    } finally {
      setBusy(false)
    }
  }

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    void submit()
  }

  // 按勾选 Skill 改写当前提示词；若优化丢掉结构前缀则补回（写入框内，非生图时静默携带）
  const optimizePrompt = async () => {
    if (!canOptimize) return
    setOptimizing(true)
    try {
      const result = await optimizePromptWithSkills({
        project_id: projectId,
        prompt,
        skill_ids: selectedIds,
        task: isVideo ? 'video_prompt' : 'image_prompt',
      })
      let next = (result.prompt || '').trim()
      if (next && !isVideo) {
        next = ensureStructureInPrompt(next, kind)
      }
      if (next) {
        setPrompt(next)
        updateNodePrompt(nodeId, next)
      }
    } catch (err) {
      setErrorMessage(err instanceof Error ? err.message : '描述优化失败')
    } finally {
      setOptimizing(false)
    }
  }

  return (
    <form
      className={`fc-generate-panel nodrag nopan nowheel${hasMedia ? ' has-media' : ''}`}
      onMouseDown={stopFlowEvent}
      onPointerDown={stopFlowEvent}
      onClick={stopFlowEvent}
      onSubmit={handleSubmit}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          updateNodePrompt(nodeId, prompt)
        }
      }}
    >
      <div className="fc-generate-head">
        <Sparkles size={14} strokeWidth={1.8} />
        <span>{copy.title}</span>
        {hasMedia ? <em className="fc-generate-tag">可再次生成</em> : null}
        {isVideo ? (
          <button
            type="button"
            className="fc-generate-help"
            title="Seedance 传值与使用规则"
            aria-label="Seedance 传值与使用规则"
            disabled={isBusy}
            onClick={() => setRulesOpen(true)}
          >
            <CircleHelp size={14} strokeWidth={1.8} />
          </button>
        ) : null}
      </div>
      <CanvasPromptEditor
        value={prompt}
        placeholder={copy.placeholder}
        disabled={isBusy}
        allowMention={allowMention}
        mentionItems={mentionItems}
        onChange={(next) => {
          setPrompt(next)
        }}
        onSubmit={() => void submit()}
      />
      {isVideo ? (
        <DramaVideoGenOptionsBar
          value={videoOpts}
          disabled={isBusy}
          onChange={(next) => {
            setVideoOpts(next)
            updateNodeVideoOptions(nodeId, next)
          }}
        />
      ) : (
        <DramaImageGenOptionsBar
          value={imageOptions}
          disabled={isBusy}
          onChange={(next) => {
            setImageOptions(next)
            updateNodeImageOptions(nodeId, next)
          }}
        />
      )}
      <DramaSkillOptionsBar
        skills={skills}
        selectedIds={selectedIds}
        disabled={isBusy}
        onToggle={toggleSkill}
        onSelectAll={selectAll}
        onSelectNone={selectNone}
        onUpload={(file) => void uploadSkill(file)}
        uploading={uploading}
        uploadError={uploadError}
      />
      <div className="fc-generate-actions">
        <span className="fc-generate-hint">{copy.hint}</span>
        <div className="fc-generate-action-btns">
          <button
            type="button"
            className="fc-generate-optimize"
            disabled={!canOptimize}
            title={
              selectedIds.length
                ? '优化画面描述，并保留台词与 @ 引用'
                : '请先选择 Skill'
            }
            onClick={() => void optimizePrompt()}
          >
            {optimizing ? <Loader2 size={14} className="fc-spin" /> : <Wand2 size={14} strokeWidth={1.8} />}
            描述优化
          </button>
          <button type="submit" className="fc-generate-submit" disabled={!canSubmit} aria-label="生成">
            {isBusy && !optimizing ? (
              <Loader2 size={16} className="fc-spin" />
            ) : (
              <ArrowUp size={16} strokeWidth={2} />
            )}
          </button>
        </div>
      </div>
      {isVideo ? <SeedanceRulesModal open={rulesOpen} onClose={() => setRulesOpen(false)} /> : null}
    </form>
  )
}
