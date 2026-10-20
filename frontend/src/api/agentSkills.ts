/** Agent Skill API：列表 / 上传 / 启用 / 删除 */

import { createJsonRequester, uploadFile } from './client'

/** Skill API 保留本模块的错误文案，避免依赖漫剧客户端。 */
const request = createJsonRequester((_response, detail) => {
  throw new Error(typeof detail === 'string' ? detail : '请求失败')
})

export type AgentSkill = {
  id: number
  slug: string
  name: string
  description: string
  tasks: string[]
  is_builtin: boolean
  is_active: boolean
  user_id: number | null
  body: string
  created_at?: string | null
  updated_at?: string | null
}

/** 列出内置 + 当前用户 Skill */
export function listAgentSkills() {
  return request<{ items: AgentSkill[] }>('/api/drama/skills')
}

/** 上传 markdown 文本 */
export function uploadAgentSkillMarkdown(markdown: string) {
  return request<AgentSkill>('/api/drama/skills', {
    method: 'POST',
    body: JSON.stringify({ markdown }),
  })
}

/** 上传 .md 文件 */
export function uploadAgentSkillFile(file: File) {
  return uploadFile<AgentSkill>('/api/drama/skills/upload', file, (_response, detail) => {
    throw new Error(typeof detail === 'string' ? detail : '上传失败')
  })
}

/** 启用/停用或改正文 */
export function patchAgentSkill(skillId: number, body: { markdown?: string; is_active?: boolean }) {
  return request<AgentSkill>(`/api/drama/skills/${skillId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

/** 删除用户 Skill（内置不可删） */
export function deleteAgentSkill(skillId: number) {
  return request<{ ok: boolean }>(`/api/drama/skills/${skillId}`, { method: 'DELETE' })
}

/** 描述优化：按勾选 Skill 改写提示词（保留 @asset 与台词） */
export function optimizePromptWithSkills(body: {
  prompt: string
  skill_ids: number[]
  task?: 'video_prompt' | 'image_prompt' | 'shot_plan'
}) {
  return request<{ prompt: string }>('/api/drama/skills/optimize', {
    method: 'POST',
    body: JSON.stringify({
      prompt: body.prompt,
      skill_ids: body.skill_ids,
      task: body.task || 'video_prompt',
    }),
  })
}
