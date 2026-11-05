/* 翻译语言和结果统计共用规则；无文字项完成后不产生可下载成图。 */
export type TranslationLanguage = 'zh' | 'en' | 'ja'

/** 给出与当前文案语言不同的翻译目标。 */
export function translationTargets(language: string): TranslationLanguage[] {
  return (['zh', 'en', 'ja'] as const).filter((target) => target !== language)
}

/** 分别统计终态进度、可下载图片和无文字跳过项。 */
export function translationProgress(items: readonly { status: string; result_url: string | null }[]): { finished: number; downloadable: number; skipped: number } {
  const skipped = items.filter((item) => item.status === 'skipped').length
  const downloadable = items.filter((item) => item.status === 'succeeded' && item.result_url).length
  return { finished: downloadable + skipped, downloadable, skipped }
}

/** 完整成功的套图有底图与文案时，才显示翻译入口。 */
export function canTranslateSet(set: { status: string; items: readonly { status: string; base_url: string | null; title: string; subtitle: string; bullets: string[] }[] }): boolean {
  return set.status === 'done' && set.items.length > 0
    && set.items.every((item) => item.status === 'succeeded' && item.base_url)
    && set.items.some((item) => item.title.trim() || item.subtitle.trim() || item.bullets.some((text) => text.trim()))
}
