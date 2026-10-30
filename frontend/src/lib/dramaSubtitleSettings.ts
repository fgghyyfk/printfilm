/** 分集字幕生成方式：兼容历史设置，并同步分镜正文中的模型字幕提示词。 */

export type DramaSubtitleMode = 'model' | 'post'

// 判断当前字幕方式是否由模型直接出字幕。
export function subtitleModeUsesModelOutput(mode: DramaSubtitleMode): boolean {
  return mode === 'model'
}

// 兼容历史布尔值，读取分集字幕方式；默认后期拼接字幕。
export function readEpisodeSubtitleMode(
  params: Record<string, unknown> | null | undefined,
): DramaSubtitleMode {
  const mode = params?.subtitleMode
  if (mode === 'model' || mode === 'post') return mode
  return readEpisodeSubtitleEnabled(params) ? 'model' : 'post'
}

// 兼容历史字符串/数字布尔值，默认关闭模型烧录字幕（后期拼接）。
export function readEpisodeSubtitleEnabled(params: Record<string, unknown> | null | undefined): boolean {
  const value = params?.subtitleEnabled
  if (value == null) return false
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value !== 0
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (['0', 'false', 'no', 'off', ''].includes(normalized)) return false
    if (['1', 'true', 'yes', 'on'].includes(normalized)) return true
  }
  return Boolean(value)
}

const DRAMA_SUBTITLE_CUE = '【字幕：底部居中·简体中文·逐句轮换·与口播同步】'
const LEGACY_SUBTITLE_CUES = [
  '【字幕：底部居中·简体中文·仅标记段落同步】',
  '【字幕：底部居中·简体中文】',
  '【字幕：全程简体中文字幕，旁白逐句同步烧录】',
]

const STRIP_PREFIX_MAP: Array<[string, string]> = [
  ['【对白·慢速清晰·同步字幕】', '【对白·慢速清晰】'],
  ['【旁白·慢速清晰·同步字幕】', '【旁白·慢速清晰】'],
  ['【旁白·自然语速·同步字幕】', '【旁白·自然语速】'],
  ['【内心独白·同步字幕】', '【内心独白】'],
]

// 判断是否为字幕 cue 行（含历史文案）。
function isSubtitleCueLine(line: string): boolean {
  const trimmed = line.trim()
  if (!trimmed.startsWith('【字幕')) return false
  return (
    trimmed === DRAMA_SUBTITLE_CUE ||
    LEGACY_SUBTITLE_CUES.includes(trimmed) ||
    /同步|烧录|底部居中/.test(trimmed)
  )
}

// 从单条分镜正文去掉模型字幕提示词，保留对白/旁白本身。
export function stripSubtitlePromptsFromContent(content: string): string {
  const lines = String(content || '')
    .replace(/\r\n/g, '\n')
    .split('\n')
  const next: string[] = []
  for (const raw of lines) {
    const trimmed = raw.trim()
    if (!trimmed) {
      next.push(raw)
      continue
    }
    if (isSubtitleCueLine(trimmed)) continue
    let line = trimmed
    for (const [src, dest] of STRIP_PREFIX_MAP) {
      if (line.startsWith(src)) {
        line = line.replace(src, dest)
        break
      }
    }
    next.push(line)
  }
  return next.join('\n').replace(/\n{3,}/g, '\n\n').trimEnd()
}

// 为单条分镜正文补回模型字幕提示词（已有则不重复）。
export function applySubtitlePromptsToContent(content: string): string {
  const source = String(content || '').replace(/\r\n/g, '\n')
  if (!source.trim()) return source
  const lines = source.split('\n')
  const next: string[] = []
  let hasCue = false
  for (const raw of lines) {
    const trimmed = raw.trim()
    if (!trimmed) {
      next.push(raw)
      continue
    }
    if (isSubtitleCueLine(trimmed)) {
      if (!hasCue) {
        next.push(DRAMA_SUBTITLE_CUE)
        hasCue = true
      }
      continue
    }
    let line = trimmed
    for (const [withSub, withoutSub] of STRIP_PREFIX_MAP) {
      if (line.startsWith(withoutSub) && !line.startsWith(withSub)) {
        line = line.replace(withoutSub, withSub)
        break
      }
    }
    next.push(line)
  }
  if (!hasCue) {
    const insertAt = next.findIndex((line) => line.trim().startsWith('【BGM'))
    if (insertAt >= 0) next.splice(insertAt, 0, DRAMA_SUBTITLE_CUE)
    else next.unshift(DRAMA_SUBTITLE_CUE)
  }
  return next.join('\n').replace(/\n{3,}/g, '\n\n').trimEnd()
}

// 按字幕方式批量改写分镜正文。
export function applySubtitleModeToFragments<T extends { content?: string | null }>(
  fragments: T[],
  mode: DramaSubtitleMode,
): T[] {
  const transform =
    mode === 'model' ? applySubtitlePromptsToContent : stripSubtitlePromptsFromContent
  return fragments.map((fragment) => {
    const prev = String(fragment.content || '')
    const next = transform(prev)
    if (next === prev) return fragment
    return { ...fragment, content: next }
  })
}
