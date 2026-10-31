/** 帮助中心：类型与常见问题搜索（文案在 i18n 的 help 段） */

export type HelpFaqItem = {
  q: string
  a: string
}

export type HelpCatItem = {
  id: string
  title: string
  desc: string
  href: string
}

export type HelpGuideStep = {
  n: string
  title: string
  body: string
}

// 按关键词过滤常见问题
export function filterHelpFaq(items: HelpFaqItem[], raw: string): HelpFaqItem[] {
  const needle = raw.trim().toLowerCase()
  if (!needle) return items
  return items.filter(
    (item) => item.q.toLowerCase().includes(needle) || item.a.toLowerCase().includes(needle),
  )
}
