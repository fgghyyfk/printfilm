import type { ImageStyleId } from './dramaImageStyles'

export type ProjectCreateKind = 'drama' | 'canvas'

export type ProjectCreateValues = {
  title: string
  source: string
  description: string
  episodeCount: number
  imageStyleId: ImageStyleId | ''
}

export type ProjectCreateDraft = Omit<ProjectCreateValues, 'episodeCount'> & { episodeCount: string }
export type ProjectCreateErrors = Partial<Record<'title' | 'source' | 'description' | 'episodeCount', string>>

export type ProjectCreateConstraints = {
  kind: ProjectCreateKind
  titleRequired?: boolean
  titleMaxLength?: number
  sourceMaxLength?: number
}

/** Reject invalid input before creating a project; never clamp an entered episode count. */
export function validateProjectCreate(
  draft: ProjectCreateDraft,
  { kind, titleRequired = false, titleMaxLength = 80, sourceMaxLength = 2000 }: ProjectCreateConstraints,
): { values: ProjectCreateValues; errors: ProjectCreateErrors } {
  const title = draft.title.trim()
  const source = draft.source.trim()
  const description = draft.description.trim()
  const episodeCount = kind === 'canvas' ? 1 : Number(draft.episodeCount)
  const errors: ProjectCreateErrors = {}

  if ((kind === 'canvas' || titleRequired) && !title) errors.title = kind === 'canvas' ? '请填写画布名称' : '请填写项目名称'
  else if (title.length > titleMaxLength) errors.title = `名称请控制在 ${titleMaxLength} 字以内`
  if (description.length > 500) errors.description = '简介请控制在 500 字以内'
  if (kind === 'drama') {
    if (source.length < 20) errors.source = '故事内容至少 20 字，请补充人物、情节或冲突'
    else if (source.length > sourceMaxLength) errors.source = `故事内容请控制在 ${sourceMaxLength} 字以内`
    if (!/^\d+$/.test(draft.episodeCount) || !Number.isInteger(episodeCount) || episodeCount < 1 || episodeCount > 120) {
      errors.episodeCount = '请输入 1–120 之间的整数集数'
    }
  }

  return {
    values: { title, source: kind === 'canvas' ? '' : source, description, episodeCount, imageStyleId: kind === 'canvas' ? '' : draft.imageStyleId },
    errors,
  }
}
