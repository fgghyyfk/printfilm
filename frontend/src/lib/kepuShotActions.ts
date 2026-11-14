/** 科普/数字人分镜操作的纯规则：阶段判定、单镜动作选择与批量重配音。 */
import { isDaihuoProject, isVideoSkipped } from './daihuoMotion.ts'

export type KepuBillingPhase = 'script' | 'assets' | 'videos' | 'compose'

/** 与后端 resolve_kepu_billing_phase 对齐（前端不做近静音文件检测）；隐私跳过的镜头算视频已收敛。 */
export function kepuBillingPhase(project: {
  pipeline_mode?: string | null
  template_id?: string | null
  ecom_set_id?: number | null
  shots?: Array<{
    image_url?: string | null
    audio_url?: string | null
    video_url?: string | null
    video_skip_reason?: string | null
  }>
}): KepuBillingPhase {
  const shots = project.shots || []
  if (!shots.length) return 'script'
  const full = project.pipeline_mode !== 'image_text'
  // 数字人每段要自己的配音；带货动态展示一律后期配音（视频内置口播只在科普 AI 视频模式）
  const separateAudio = !full || project.pipeline_mode === 'avatar' || isDaihuoProject(project)
  const needImages = shots.some((s) => !s.image_url)
  const needAudio = separateAudio && shots.some((s) => !s.audio_url)
  if (needImages || needAudio) return 'assets'
  if (full && shots.some((s) => !s.video_url && !isVideoSkipped(s))) return 'videos'
  return 'compose'
}

/** 单镜重绘要调的接口：数字人必须重画场景（保留同一个人），不能走普通生图。 */
export function shotImageAction(pipelineMode?: string | null): 'avatar_scene' | 'image' {
  return pipelineMode === 'avatar' ? 'avatar_scene' : 'image'
}

/** 是否存在可单独重配的配音；声音由视频模型内置生成时没有。 */
export function canRegenShotAudio(project: { native_video_audio?: boolean | null }): boolean {
  return !project.native_video_audio
}

/**
 * 批量重配音的提交方式：配音是整片级任务，同时只能有一个，所以只提交一次。
 * 数字人把其余段随同一任务带上；图文的配音本来就覆盖整片。无需提交时返回 null。
 */
export function batchRegenAudioPlan(
  project: { pipeline_mode?: string | null; native_video_audio?: boolean | null },
  shotIds: number[],
): { shotId: number; extraShotIds: number[] } | null {
  if (!shotIds.length || !canRegenShotAudio(project)) return null
  const [shotId, ...rest] = shotIds
  return { shotId, extraShotIds: project.pipeline_mode === 'avatar' ? rest : [] }
}
