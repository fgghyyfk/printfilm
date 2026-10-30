/** 文本域 @ 引用：触发检测与光标坐标测量，供生成页绑定资产参考。 */

export type PromptMentionTrigger = {
  /** @ 符号在文本中的下标 */
  start: number
  /** @ 后已输入的过滤词 */
  query: string
}

export type MentionCaretRect = { top: number; bottom: number; left: number }

/** 从光标向前查找最近的 @ 触发点：@ 与光标之间不允许空白。 */
export function detectPromptMentionTrigger(text: string, caret: number): PromptMentionTrigger | null {
  const match = /@([^\s@]*)$/.exec(text.slice(0, caret))
  if (!match) return null
  return { start: caret - match[1].length - 1, query: match[1] }
}

// 镜像节点需要复制的排版样式，保证测量位置与文本域一致
const MIRROR_STYLES = [
  'boxSizing', 'fontFamily', 'fontSize', 'fontStyle', 'fontWeight',
  'letterSpacing', 'lineHeight', 'padding', 'borderWidth', 'textTransform',
  'wordBreak', 'overflowWrap',
] as const

/** 用同样式镜像节点测量光标屏幕坐标，用于弹层定位。 */
export function measureTextareaCaretRect(textarea: HTMLTextAreaElement, position: number): MentionCaretRect {
  const style = window.getComputedStyle(textarea)
  const mirror = document.createElement('div')
  const mirrorStyle = mirror.style as unknown as Record<string, string>
  const sourceStyle = style as unknown as Record<string, string>
  for (const key of MIRROR_STYLES) mirrorStyle[key] = sourceStyle[key]
  mirrorStyle.position = 'absolute'
  mirrorStyle.visibility = 'hidden'
  mirrorStyle.left = '-9999px'
  mirrorStyle.top = '0'
  mirrorStyle.width = `${textarea.clientWidth}px`
  mirrorStyle.whiteSpace = 'pre-wrap'
  mirror.textContent = textarea.value.slice(0, position)
  const marker = document.createElement('span')
  marker.textContent = '​'
  mirror.appendChild(marker)
  document.body.appendChild(mirror)
  const rect = textarea.getBoundingClientRect()
  const top = rect.top + marker.offsetTop - textarea.scrollTop
  const left = rect.left + marker.offsetLeft
  const lineHeight = Number.parseFloat(style.lineHeight) || 20
  document.body.removeChild(mirror)
  return { top, bottom: top + lineHeight, left }
}
