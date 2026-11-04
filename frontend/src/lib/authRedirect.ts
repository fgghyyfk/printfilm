/** 只接受站内绝对路径，拒绝会被浏览器解释为外站地址的反斜杠及控制字符。 */
export function safeNextPath(raw: string | null, fallback = '/'): string {
  if (!raw || !raw.startsWith('/') || raw.startsWith('//') || raw.includes('\\')) return fallback
  for (const char of raw) {
    const code = char.charCodeAt(0)
    if (code <= 31 || (code >= 127 && code <= 159)) return fallback
  }
  return raw
}
