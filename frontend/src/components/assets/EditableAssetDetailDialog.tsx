import { useTeamEditLock } from '../../features/teams/useTeamEditLock'
/** 资产详情操作框：预览图、上传/生图提示词编辑、生成/音色、形象历史版本 */
import { useEffect, useRef, useState } from 'react'
import { dramaApi, type DramaAsset } from '../../api/drama'
import { AssetDetailDialog } from './AssetDetailDialog'
import { readVisualPrompt } from '../../lib/dramaVisualPrompt'
import { dramaAssetHasImage } from '../../lib/dramaAssetImage'
import {
  formatAssetImageVersionLabel,
  readAssetImageVersions,
  resolveAssetImageVersionUrl,
} from '../../lib/dramaAssetImageVersions'
import { readAssetVoiceBinding } from '../../pages/drama/CharacterVoiceBindModal'

type Props = {
  asset: DramaAsset
  open: boolean
  readOnly?: boolean
  busy?: boolean
  genLabel?: string
  onClose: () => void
  onUpdated: (asset: DramaAsset) => void
  onGenerate: (asset: DramaAsset) => void
  onBindVoice?: (asset: DramaAsset) => void
  onDelete?: (asset: DramaAsset) => void
  onError: (message: string) => void
}

// 将编辑后的提示词写回 params.visualPrompt
function buildPromptParams(asset: DramaAsset, prompt: string): Record<string, unknown> {
  const prev = (asset.params || {}) as Record<string, unknown>
  const kind = (asset.type || '').toLowerCase()
  const next: Record<string, unknown> = {
    ...prev,
    visualPrompt: prompt.trim(),
  }
  if (kind === 'character' || kind === 'scene') {
    next.visualImage = prompt.trim()
  }
  return next
}

// 渲染资产详情操作弹窗
export function EditableAssetDetailDialog({
  asset,
  open,
  readOnly = false,
  busy = false,
  genLabel = '生成形象',
  onClose,
  onUpdated,
  onGenerate,
  onBindVoice,
  onDelete,
  onError,
}: Props) {
  /*
   * promptDraft 提示词草稿
   * saving 保存中
   * uploading 上传图片中
   * restoringVersionId 正在还原的版本
   * lightboxSrc 放大预览图 URL
   */
  const [promptDraft, setPromptDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [restoringVersionId, setRestoringVersionId] = useState<string | null>(null)
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null)
  const uploadInputRef = useRef<HTMLInputElement>(null)

  const hasImage = dramaAssetHasImage(asset)
  const voice = readAssetVoiceBinding(asset)
  const isCharacter = (asset.type || '').toLowerCase() === 'character'
  const isScene = (asset.type || '').toLowerCase() === 'scene'
  const isProp =
    (asset.type || '').toLowerCase() === 'prop' ||
    (asset.type || '').toLowerCase() === 'material'
  const deleteLabel = isScene ? '删除场景' : isProp ? '删除道具' : '删除角色'
  const canDelete = Boolean(onDelete) && (isCharacter || isScene || isProp)
  const dirty = promptDraft.trim() !== readVisualPrompt(asset).trim()
  const imageVersions = readAssetImageVersions(asset)
  const lock = useTeamEditLock({ projectId: asset.project_id, type: 'asset', resourceId: asset.id }, open && !readOnly, async (isCurrent) => { const rows = await dramaApi.listAssets(asset.project_id); const fresh = rows.find((item) => item.id === asset.id); if (!fresh) throw new Error('资产已删除，请关闭后重新加载'); if (isCurrent()) onUpdated(fresh) })
  const actionBusy = readOnly || lock.readOnly || busy || saving || uploading || Boolean(restoringVersionId)

  useEffect(() => {
    if (!open) return
    setPromptDraft(readVisualPrompt(asset))
    setLightboxSrc(null)
    setRestoringVersionId(null)
  }, [open, asset])

  // 保存提示词到资产 params
  async function savePrompt() {
    if (actionBusy) return
    const text = promptDraft.trim()
    if (!text) {
      onError('提示词不能为空')
      return
    }
    setSaving(true)
    try {
      const updated = await dramaApi.updateAsset(asset.id, {
        params: buildPromptParams(asset, text),
      })
      onUpdated(updated)
    } catch (err) {
      onError(err instanceof Error ? err.message : '保存提示词失败')
    } finally {
      setSaving(false)
    }
  }

  // 先保存脏提示词再触发生图
  async function handleGenerate() {
    if (actionBusy) return
    if (dirty) {
      const text = promptDraft.trim()
      if (!text) {
        onError('提示词不能为空')
        return
      }
      setSaving(true)
      try {
        const updated = await dramaApi.updateAsset(asset.id, {
          params: buildPromptParams(asset, text),
        })
        onUpdated(updated)
        onGenerate(updated)
      } catch (err) {
        onError(err instanceof Error ? err.message : '保存提示词失败')
      } finally {
        setSaving(false)
      }
      return
    }
    onGenerate(asset)
  }

  // 本地上传图片，视为已出图
  async function handleUpload(file: File) {
    if (actionBusy) return
    setUploading(true)
    try {
      const updated = await dramaApi.uploadAssetMedia(asset.id, file)
      onUpdated(updated)
    } catch (err) {
      onError(err instanceof Error ? err.message : '上传失败')
    } finally {
      setUploading(false)
      if (uploadInputRef.current) uploadInputRef.current.value = ''
    }
  }

  // 将历史形象还原为当前
  async function handleRestoreVersion(versionId: string) {
    if (actionBusy) return
    setRestoringVersionId(versionId)
    try {
      const updated = await dramaApi.activateAssetImageVersion(asset.id, versionId)
      onUpdated(updated)
    } catch (err) {
      onError(err instanceof Error ? err.message : '还原失败')
    } finally {
      setRestoringVersionId(null)
    }
  }

  // 媒体详情保持统一，当前业务仅提供编辑与历史操作。
  const history = imageVersions.find((item) => resolveAssetImageVersionUrl(item) === lightboxSrc)
  return <>
    <AssetDetailDialog
      asset={asset}
      open={open}
      onClose={onClose}
      notice={readOnly ? <p className="drama-muted" role="status">只读查看</p> : lock.readOnly ? <p className="pf-team-alert" role="status">{lock.checking ? '正在取得编辑占用…' : lock.error}<button type="button" onClick={lock.retry}>重新尝试</button></p> : undefined}
      actions={!readOnly && <button type="button" className="pf-btn pf-btn-lime" disabled={actionBusy || !promptDraft.trim()} onClick={() => void handleGenerate()}>{busy ? '生成中…' : genLabel}</button>}
    >
      {!readOnly && <details className="pf-asset-detail-editor">
        <summary>编辑下次生成的提示词</summary>
        <label><span className="sr-only">生图提示词</span><textarea rows={5} value={promptDraft} readOnly={lock.readOnly} onChange={(event) => setPromptDraft(event.target.value)} placeholder="描述外观、构图、光影与风格…" /></label>
        <div className="pf-asset-detail-edit-actions">
          <button type="button" className="pf-btn pf-btn-sm" disabled={saving || !dirty || actionBusy} onClick={() => void savePrompt()}>{saving ? '保存中…' : '保存提示词'}</button>
        </div>
      </details>}
      {!readOnly && <div className="pf-asset-detail-edit-actions">
        <input ref={uploadInputRef} type="file" accept="image/jpeg,image/png,image/webp,image/gif" className="sr-only" disabled={actionBusy} onChange={(event) => { const file = event.target.files?.[0]; if (file) void handleUpload(file) }} />
        <button type="button" className="pf-btn pf-btn-sm" disabled={actionBusy} onClick={() => uploadInputRef.current?.click()}>{uploading ? '上传中…' : hasImage ? '更换图片' : '上传图片'}</button>
        {isCharacter && onBindVoice && <button type="button" className="pf-btn pf-btn-sm" disabled={actionBusy} onClick={() => onBindVoice(asset)}>{voice ? '更换音色' : '绑定音色'}</button>}
        {canDelete && <button type="button" className="pf-btn pf-btn-sm drama-btn-danger-text" disabled={actionBusy} onClick={() => onDelete?.(asset)}>{deleteLabel}</button>}
      </div>}
      {imageVersions.length > 0 && <details className="pf-asset-detail-history">
        <summary>历史版本 · {imageVersions.length} 个</summary>
        <ul className="pf-asset-detail-versions">{imageVersions.map((version) => <li key={version.id}>
          <button type="button" className="pf-asset-detail-version-thumb" title="查看历史版本详情" onClick={() => setLightboxSrc(resolveAssetImageVersionUrl(version))}><img src={resolveAssetImageVersionUrl(version)} alt="" /></button>
          <div className="pf-asset-detail-version-info"><span>{formatAssetImageVersionLabel(version)}</span>{version.createdAt && <small>{version.createdAt.replace('T', ' ').slice(0, 16)}</small>}</div>
          {!readOnly && <button type="button" className="pf-btn pf-btn-sm" disabled={actionBusy} onClick={() => void handleRestoreVersion(version.id)}>{restoringVersionId === version.id ? '还原中…' : '还原'}</button>}
        </li>)}</ul>
      </details>}
    </AssetDetailDialog>
    {lightboxSrc && <AssetDetailDialog asset={{ ...asset, name: `${asset.name || '资产'} · 历史版本`, cover: lightboxSrc, url: lightboxSrc, params: { media_metadata: history?.mediaMetadata || { source: 'unknown' } } }} onClose={() => setLightboxSrc(null)} />}
  </>
}
