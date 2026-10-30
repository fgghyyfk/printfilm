export const SHORT_VIDEO_LIST_PATH = '/history'
export const SHORT_VIDEO_CREATE_PATH = '/studio/new'

/** 旧书签进入卡片列表；带创作参数的链接仍直接打开对应工作区。 */
export function legacyStudioEntryPath(params: { project?: string | null; template?: string | null; mode?: string | null }): string {
  if (params.project && /^[1-9]\d*$/.test(params.project)) return `/studio/${params.project}`
  if (!params.template && !params.mode) return SHORT_VIDEO_LIST_PATH
  const query = new URLSearchParams()
  if (params.template) query.set('template', params.template)
  if (params.mode) query.set('mode', params.mode)
  return `${SHORT_VIDEO_CREATE_PATH}?${query.toString()}`
}

export function shortVideoCreatePath(templateId?: string): string {
  return templateId
    ? `${SHORT_VIDEO_CREATE_PATH}?template=${encodeURIComponent(templateId)}`
    : SHORT_VIDEO_CREATE_PATH
}

/** Existing creation sessions use their recovery route; regular projects open the step editor. */
export function shortVideoProjectPath(project: {
  id: number
  status: string
  creation_session_id?: number | null
  pipeline_mode?: string | null
}): string {
  if (project.creation_session_id) return `/studio/sessions/${project.creation_session_id}?project=${project.id}`
  if (project.status === 'DRAFT' && project.pipeline_mode === 'image_text') return `/studio/new?project=${project.id}`
  return project.status === 'DRAFT' ? `/studio/${project.id}/style` : `/studio/${project.id}`
}
