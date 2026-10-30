import type { Template } from '../api'

/** 新建短片的公开精选名单；历史项目仍通过 getTemplate(id) 读取原模板。 */
export const CREATION_TEMPLATE_IDS = [
  'anim_3d',
  'soft_anime',
  'kids_flat',
  'ink_guofeng',
] as const

export const DEFAULT_CREATION_TEMPLATE_ID = CREATION_TEMPLATE_IDS[0]

const PRESENTATION = {
  anim_3d: {
    name: '3D 动画',
    description: '圆润的三维角色与柔和光影，适合知识讲解和故事短片。',
    category: ['3D', '动画'],
  },
  soft_anime: {
    name: '二维动漫',
    description: '干净线稿与柔和赛璐璐色彩，适合青春故事和情感表达。',
    category: ['二维', '动漫'],
  },
  kids_flat: {
    name: '绘本插画',
    description: '柔和粉彩与亲切的插画角色，适合儿童科普和温暖故事。',
    category: ['绘本', '插画'],
  },
  ink_guofeng: {
    name: '国风水墨',
    description: '水墨留白与写意笔触，适合历史文化和诗意故事。',
    category: ['国风', '水墨'],
  },
}

export function creationTemplateId(id?: string | null): string {
  return CREATION_TEMPLATE_IDS.find((candidate) => candidate === id)
    ?? DEFAULT_CREATION_TEMPLATE_ID
}

/** 兼容尚未更新的服务端列表，同时保留真实素材与模型配置，不伪造可用模板。 */
export function curatedCreationTemplates(templates: Template[]): Template[] {
  const byId = new Map(templates.map((template) => [template.id, template]))
  return CREATION_TEMPLATE_IDS.flatMap((id, sort_order) => {
    const template = byId.get(id)
    if (!template) return []
    const presentation = PRESENTATION[id]
    return [{
      ...template,
      ...presentation,
      category: [...presentation.category],
      default_ratio: '9:16',
      sort_order,
    }]
  })
}
