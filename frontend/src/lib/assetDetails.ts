/** 统一资产详情数据：成功媒体快照优先，上传素材不混入旧生成参数。 */
export type AssetDetailRecord = {
  id?: number
  name?: string | null
  type: string
  asset_type?: string
  cover?: string | null
  url?: string | null
  params?: Record<string, unknown> | null
  project_id?: number | null
  updated_at?: string | null
}

/** 只处理普通对象，兼容历史脏数据。 */
function object(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

/** 仅展示标量值；保留 false 和 0，避免把内部对象直接输出到页面。 */
function display(value: unknown): string {
  if (typeof value === 'boolean') return value ? '是' : '否'
  return typeof value === 'string' || typeof value === 'number' ? String(value) : ''
}

/** 从当前媒体读取生成参数；新快照存在时绝不回退到旧模型。 */
export function assetDetailInfo(asset: AssetDetailRecord) {
  const params = object(asset.params)
  const gen = object(params.generation)
  const snapshot = object(params.media_metadata)
  const hasSnapshot = params.media_metadata != null && typeof params.media_metadata === 'object'
  const uploaded = (hasSnapshot ? snapshot.source : gen.source) === 'upload'
  const options = hasSnapshot ? object(snapshot.parameters) : { ...params, ...object(params.videoOptions), ...gen }
  const model = uploaded ? '' : display(hasSnapshot ? snapshot.model_id : options.model_id)
  const source = uploaded ? '上传' : (hasSnapshot ? snapshot.source === 'generated' : Boolean(model)) ? 'AI 生成' : '未记录'
  const prompt = display(hasSnapshot ? snapshot.prompt : params.visualPrompt || params.visualImage || params.voicePrompt || params.prompt || params.description)
  const labels: Record<string, string> = {
    aspect_ratio: '比例', resolution: '分辨率', size: '尺寸', duration_sec: '时长', duration: '时长',
    image_style_id: '画面风格', generate_audio: '生成声音', watermark: '水印', camera_fixed: '固定镜头',
    seed: '随机种子', speaker: '音色', sample_text: '试听文本', sampleText: '试听文本',
  }
  const parameters = uploaded ? [] : Object.entries(labels).flatMap(([key, label]) => {
    const value = display(options[key])
    return value ? [{ key, label, value: key.startsWith('duration') ? `${value} 秒` : value }] : []
  })
  return {
    source, model, prompt, parameters,
    filename: display(snapshot.filename), fileSize: typeof snapshot.file_size === 'number' ? snapshot.file_size : null,
    createdAt: display(snapshot.created_at || gen.finished_at || asset.updated_at),
  }
}
