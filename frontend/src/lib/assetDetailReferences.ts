import { CONTENT_TOKEN_PATTERN } from './dramaEpisodePromptEditor.ts'

/** Authorized reference media, separate from a generation's downloadable outputs. */
export type AssetDetailReference = {
  kind: 'image' | 'video' | 'audio'
  url: string
  name?: string | null
  asset_id?: number | null
  cover?: string | null
  role?: string | null
}

export type AssetDetailPromptPart =
  | { kind: 'text'; text: string }
  | { kind: 'asset'; text: string; assetId: number; references: AssetDetailReference[] }

function mediaUrl(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  const url = value.trim()
  if (!url || url.length > 8192 || /[\\\u0000-\u001f\u007f]/.test(url) || /\[redacted\]/i.test(url)) return undefined
  try {
    const parsed = new URL(url, 'https://local.invalid')
    if (/^https?:\/\//i.test(url) && parsed.hostname && !parsed.username && !parsed.password) return url
    if (/^\/(?:static|media|uploads)\//.test(url) && /^\/(?:static|media|uploads)\//.test(parsed.pathname) && !url.split(/[?#]/, 1)[0].split('/').includes('..')) return url
  } catch { /* Malformed persisted reference metadata is not renderable. */ }
  return undefined
}

/** Keep backend order, repeated roles and all media kinds for the same asset ID. */
export function normalizeAssetDetailReferences(value: unknown): AssetDetailReference[] {
  if (!Array.isArray(value)) return []
  return value.flatMap(item => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const row = item as Record<string, unknown>
    if (row.kind !== 'image' && row.kind !== 'video' && row.kind !== 'audio') return []
    const url = mediaUrl(row.url)
    if (!url) return []
    const name = typeof row.name === 'string' ? row.name.trim() : ''
    const role = typeof row.role === 'string' ? row.role.trim() : ''
    const assetId = typeof row.asset_id === 'number' && Number.isSafeInteger(row.asset_id) && row.asset_id > 0 ? row.asset_id : undefined
    const cover = mediaUrl(row.cover)
    const reference: AssetDetailReference = {
      kind: row.kind, url,
      ...(name ? { name } : {}),
      ...(assetId ? { asset_id: assetId } : {}),
      ...(cover ? { cover } : {}),
      ...(role ? { role } : {}),
    }
    return [reference]
  })
}

/** Reuse editor token syntax; unresolved mentions and @duration remain literal text. */
export function splitAssetDetailPrompt(prompt: string, references: AssetDetailReference[]): AssetDetailPromptPart[] {
  const parts: AssetDetailPromptPart[] = []
  const pattern = new RegExp(CONTENT_TOKEN_PATTERN.source, 'g')
  let last = 0
  let match: RegExpExecArray | null
  while ((match = pattern.exec(prompt))) {
    if (!match[1].startsWith('asset:')) continue
    const assetId = Number(match[1].slice('asset:'.length))
    const matches = references.filter(reference => reference.asset_id === assetId)
    if (!matches.length) continue
    if (match.index > last) parts.push({ kind: 'text', text: prompt.slice(last, match.index) })
    parts.push({ kind: 'asset', text: match[0], assetId, references: matches })
    last = match.index + match[0].length
  }
  if (last < prompt.length) parts.push({ kind: 'text', text: prompt.slice(last) })
  return parts
}
