/** Four manual checkpoints for the image + narration short-video workflow. */
export const IMAGE_TEXT_STEPS = [
  { key: 'topic', label: '创作设置' },
  { key: 'confirm', label: '文案与分镜' },
  { key: 'assets', label: '画面与配音' },
  { key: 'compose', label: '成片预览' },
]

type ImageTextProject = {
  status: string
  progress?: number
  final_video_url?: string | null
  shots?: Array<{ image_url?: string | null; audio_url?: string | null }>
}

export function imageTextAssetReadiness(project: ImageTextProject) {
  const shots = project.shots || []
  const images = shots.filter((shot) => Boolean(shot.image_url)).length
  const audio = shots.filter((shot) => Boolean(shot.audio_url)).length
  return { total: shots.length, images, audio, ready: shots.length > 0 && images === shots.length && audio === shots.length }
}

/** Finishing assets stays on step three until the user starts composition. */
export function imageTextBoardStep(project: ImageTextProject): 1 | 2 | 3 {
  if (project.final_video_url || ['COMPOSING', 'AUDITING', 'DONE'].includes(project.status)) return 3
  const assets = imageTextAssetReadiness(project)
  if (
    assets.images > 0 || assets.audio > 0 ||
    ['IMAGING', 'AUDIOING', 'IMAGE_READY', 'AUDIO_READY', 'PARALLEL_ASSETS', 'VIDEO_READY'].includes(project.status) ||
    (assets.total > 0 && (project.progress || 0) >= 18)
  ) return 2
  return 1
}

export function imageTextNextAction(project: ImageTextProject): 'script' | 'assets' | 'compose' | 'preview' {
  if (project.final_video_url) return 'preview'
  if (!project.shots?.length) return 'script'
  return imageTextAssetReadiness(project).ready ? 'compose' : 'assets'
}
