/** 资产目录：项目素材、分镜与成片，以及无项目的独立生成结果。 */
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { Pause, Play, Image } from 'lucide-react'
import { AssetBanner, AssetCard, AssetEmpty, AssetGrid, AssetSearch } from '../../components/assets/AssetLibrary'
import MediaLibraryFilters, { type MediaLibraryFilter } from '../../components/assets/MediaLibraryFilters'
import BillingErrorNotice from '../../components/billing/BillingErrorNotice'
import AppShell from '../../components/layout/AppShell'
import Button from '../../components/ui/Button'
import FilterSelect from '../../components/ui/FilterSelect'
import Pagination from '../../components/ui/Pagination'
import { api } from '../../api'
import { CharacterVoicePreviewButton } from '../../components/drama/CharacterVoicePreviewButton'
import { dramaApi, resolveDramaMediaUrl, type MediaLibraryItem, type MediaLibraryPage } from '../../api/drama'
import { readAssetVoiceBinding } from './CharacterVoiceBindModal'
import { AssetDetailDialog } from '../../components/assets/AssetDetailDialog'
import { DRAMA_VOICE_BINDING_ENABLED } from '../../lib/dramaVoiceBinding'
import { pageCountOf } from '../../lib/pagination'
import { subscribeWorkspace, workspaceScope } from '../../features/teams/workspaceScope'
import RequireAuth from './RequireAuth'
import { useI18n, type TFunction } from '../../i18n'
import { useNavigate } from 'react-router-dom'
import EcomSetBatches from '../../components/ecomSet/EcomSetBatches'
import '../../styles/tools-base.css'
import './drama.css'

const PAGE_SIZE_OPTIONS = [12, 24, 36] as const
type LibraryProject = { id: number; title: string; kind: 'drama' | 'short_video' }

/** 漫剧与短视频使用独立项目编号空间，不能只传数字筛选。 */
function projectSelection(value: string): { unassigned: boolean; projectId?: number; projectKind?: LibraryProject['kind'] } {
  const [kind, rawId] = value.split(':')
  const id = Number(rawId)
  return {
    unassigned: value === 'unassigned',
    ...(Number.isInteger(id) && id > 0 && (kind === 'drama' || kind === 'short_video')
      ? { projectId: id, projectKind: kind } : {}),
  }
}

export default function AssetLibraryPage() {
  const workspaceEpoch = useSyncExternalStore(subscribeWorkspace, () => workspaceScope.epoch)
  return <RequireAuth><AssetLibraryInner key={workspaceEpoch} /></RequireAuth>
}

function previewAudio(asset: MediaLibraryItem): string {
  return asset.media_kind === 'audio' ? asset.url || '' : readAssetVoiceBinding(asset)?.url || ''
}

/** 把当前页资产按「来源项目 + 生成时间（30 分钟内算同一批）」分组，保持原有顺序；t 用于无项目标题时的兜底文案 */
function groupByBatch(items: MediaLibraryItem[], t: TFunction): { key: string; title: string; time: string; items: MediaLibraryItem[] }[] {
  const groups: { key: string; title: string; time: string; items: MediaLibraryItem[] }[] = []
  for (const item of items) {
    const at = item.updated_at ? new Date(item.updated_at) : null
    const bucket = at ? Math.floor(at.getTime() / (30 * 60 * 1000)) : 0
    const key = `${item.project_kind || ''}:${item.project_id || 0}:${bucket}`
    const last = groups[groups.length - 1]
    if (last && last.key === key) { last.items.push(item); continue }
    groups.push({ key, title: item.project_title || t(item.project_id ? 'assetLibraryPage.projectAsset' : 'assetLibraryPage.unassigned'),
      time: at ? at.toLocaleString() : '', items: [item] })
  }
  return groups
}

function AssetLibraryInner() {
  const navigate = useNavigate()
  const { t } = useI18n()
  // 展示方式：电商图套图（按批次）/ 资产按批次分组 / 全部平铺
  const [view, setView] = useState<'sets' | 'batch' | 'grid'>('batch')
  const [listing, setListing] = useState<MediaLibraryPage>({ items: [], meta: { total: 0, total_all: 0, page: 1, page_size: 12 } })
  const [projects, setProjects] = useState<LibraryProject[]>([])
  const [projectId, setProjectId] = useState('')
  const [mediaFilter, setMediaFilter] = useState<MediaLibraryFilter>({ kind: 'image', imageType: 'all', generationStatus: 'all' })
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(12)
  const [playingKey, setPlayingKey] = useState<string | null>(null)
  const [selectedAsset, setSelectedAsset] = useState<MediaLibraryItem | null>(null)
  const [error, setError] = useState('')
  const [playbackError, setPlaybackError] = useState('')
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const playbackEpoch = useRef(0)

  useEffect(() => {
    let active = true
    /** 分别读取两类项目，团队范围不列出个人短视频项目。 */
    async function loadProjects(kind: LibraryProject['kind']) {
      const rows: LibraryProject[] = []
      let page = 1
      while (active) {
        const result = kind === 'drama' ? await dramaApi.listProjects({ page, page_size: 50 }) : await api.listProjects({ page, page_size: 50 })
        if (!active) return []
        rows.push(...result.items.map((project) => ({ id: project.id, title: project.title || t('assetLibraryPage.untitledProject'), kind })))
        if (rows.length >= result.meta.total || result.items.length === 0) break
        page += 1
      }
      return rows
    }
    const kinds: LibraryProject['kind'][] = workspaceScope.teamId === null ? ['drama', 'short_video'] : ['drama']
    void Promise.allSettled(kinds.map(loadProjects)).then((results) => {
      if (active) setProjects(results.flatMap((result) => result.status === 'fulfilled' ? result.value : []))
    })
    return () => { active = false }
  }, [t])

  useEffect(() => {
    const timer = window.setTimeout(() => { setSearch(query.trim().slice(0, 200)); setPage(1) }, 250)
    return () => window.clearTimeout(timer)
  }, [query])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError('')
    dramaApi.listLibraryAssets({ type: mediaFilter.kind === 'image' && mediaFilter.imageType !== 'all' ? mediaFilter.imageType : mediaFilter.kind,
      mediaKind: mediaFilter.kind, generationStatus: mediaFilter.generationStatus, ...projectSelection(projectId), q: search, page, pageSize }, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setListing(result) })
      .catch((cause) => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : t('common.loadFailed')) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [mediaFilter, projectId, search, page, pageSize, revision, t])

  useEffect(() => {
    playbackEpoch.current += 1
    audioRef.current?.pause()
    setPlayingKey(null)
    setPlaybackError('')
    return () => { playbackEpoch.current += 1; audioRef.current?.pause() }
  }, [mediaFilter, projectId, search, page, pageSize])

  const projectOptions = useMemo(() => [
    { value: '', label: t('assetLibraryPage.allProjects') }, { value: 'unassigned', label: t('assetLibraryPage.unassigned') },
    ...projects.map((project) => ({ value: `${project.kind}:${project.id}`, label: `${t(project.kind === 'drama' ? 'assetLibraryPage.kindDrama' : 'assetLibraryPage.kindShortVideo')} · ${project.title}` })),
  ], [projects, t])
  const pageCount = pageCountOf(listing.meta.total, pageSize)

  async function toggleAudio(asset: MediaLibraryItem) {
    const epoch = ++playbackEpoch.current
    audioRef.current?.pause()
    setPlaybackError('')
    if (playingKey === asset.key) { setPlayingKey(null); return }
    const src = resolveDramaMediaUrl(previewAudio(asset))
    if (!src) return
    const audio = audioRef.current || new Audio()
    audioRef.current = audio
    audio.src = src
    audio.onended = () => { if (epoch === playbackEpoch.current) setPlayingKey(null) }
    setPlayingKey(asset.key)
    try { await audio.play() }
    catch { if (epoch === playbackEpoch.current) { setPlayingKey(null); setPlaybackError(t('assetLibraryPage.playFailed')) } }
  }

  return <AppShell active="assets">
    <div className="drama-page pf-library-panel">
      <header className="pf-library-page-head">
        <div className="pf-library-heading"><div><h1>{t('assetLibraryPage.title')}</h1><p className="pf-muted" style={{ margin: '0.35rem 0 0' }}>{t('assetLibraryPage.subtitle')}</p></div>
          <span className="pf-library-count"><Image size={14} />{t('assetLibraryPage.count', { count: listing.meta.total })}</span>
        </div>
        <AssetBanner>{t('assetLibraryPage.banner')}</AssetBanner>
        <div className="pf-library-toolbar pf-library-toolbar--hierarchical">
          <MediaLibraryFilters value={mediaFilter} onChange={value => { setMediaFilter(value); setPage(1) }} />
          <div className="pf-library-toolbar-end">
            <FilterSelect label={t('assetLibraryPage.projectFilter')} value={projectId} options={projectOptions} onChange={(value) => { setProjectId(value); setPage(1) }} />
            <AssetSearch value={query} onChange={setQuery} placeholder={t('assetLibraryPage.searchPlaceholder')} />
          </div>
        </div>
      </header>
      <div className="pf-library-views pf-tool-chips" role="tablist" aria-label={t('assetLibraryPage.viewsAria')}>
        {([['batch', t('assetLibraryPage.viewBatch')], ['sets', t('assetLibraryPage.viewSets')], ['grid', t('assetLibraryPage.viewGrid')]] as const).map(([id, label]) =>
          <button key={id} type="button" role="tab" aria-selected={view === id} className={view === id ? 'active' : ''} onClick={() => setView(id)}>{label}</button>)}
      </div>
      {view === 'sets' ? <EcomSetBatches onOpen={(id) => navigate(`/daihuo?id=${id}`)} /> : <>
      {error && <div role="alert"><BillingErrorNotice message={error} className="drama-error" /><Button size="sm" onClick={() => setRevision((value) => value + 1)}>{t('assetLibraryPage.reload')}</Button></div>}
      {playbackError && <p role="alert" className="drama-error">{playbackError}</p>}
      <div className="pf-library-meta"><p className="pf-muted" style={{ margin: 0 }} role="status">{loading ? t('common.loading') : t('assetLibraryPage.summary', { totalAll: listing.meta.total_all, total: listing.meta.total, page: listing.meta.page, pageCount })}</p></div>
      {loading ? <div className="pf-library-empty" aria-busy="true">{t('assetLibraryPage.loadingAssets')}</div> : error ? null : listing.items.length === 0 ? <AssetEmpty /> : <>
        {(view === 'batch' ? groupByBatch(listing.items, t) : [{ key: 'all', title: '', time: '', items: listing.items }]).map((group) => <div key={group.key} className="pf-library-batch">
        {view === 'batch' ? <h3>{group.title} <span className="pf-muted">· {group.time} · {t('assetLibraryPage.batchCount', { count: group.items.length })}</span></h3> : null}
        <AssetGrid>{group.items.map((asset) => {
          const audio = asset.media_kind === 'audio'
          const preview = previewAudio(asset)
          const playing = playingKey === asset.key
          const sourceLabel = t(asset.source_kind === 'drama_fragment' ? 'assetLibraryPage.sourceFragment'
            : asset.source_kind === 'short_video' ? 'assetLibraryPage.sourceShortVideo'
            : asset.source_kind === 'task' ? 'assetLibraryPage.sourceTask'
            : asset.source_kind === 'tool' && !asset.project_id ? 'assetLibraryPage.sourceStandalone' : 'assetLibraryPage.projectAsset')
          const workspaceLabel = t(workspaceScope.teamId === null ? 'assetLibraryPage.personalAsset' : 'assetLibraryPage.teamAsset')
          return <AssetCard key={asset.key} asset={asset} subtitle={asset.project_title || t(asset.project_id ? 'assetLibraryPage.projectAsset' : 'assetLibraryPage.unassigned')} footer={`${sourceLabel} · ${workspaceLabel}`} updatedAt={asset.updated_at} onOpen={() => setSelectedAsset(asset)}
            status={audio ? t(playing ? 'assetLibraryPage.audioPlaying' : preview ? 'assetLibraryPage.audioReady' : 'assetLibraryPage.audioPending') : undefined}
            actions={<>
              {audio && preview && <button type="button" className="pf-btn pf-btn-sm" onClick={() => void toggleAudio(asset)} aria-label={t(playing ? 'assetLibraryPage.stopPreview' : 'assetLibraryPage.previewAudioAria')}>{playing ? <Pause size={14} /> : <Play size={14} />}{t(playing ? 'assetLibraryPage.stopPreview' : 'assetLibraryPage.previewAudio')}</button>}
              {!audio && DRAMA_VOICE_BINDING_ENABLED && preview && <CharacterVoicePreviewButton url={preview} label={asset.name || undefined} onError={setPlaybackError} />}
              {asset.project_url ? <Button to={asset.project_url} variant="ghost" size="sm">{t('assetLibraryPage.openProject')}</Button> : <Button size="sm" onClick={() => setSelectedAsset(asset)}>{t('assetLibraryPage.viewAsset')}</Button>}
            </>} />
        })}</AssetGrid></div>)}
        <Pagination page={listing.meta.page} pageCount={pageCount} total={listing.meta.total} pageSize={pageSize} pageSizeOptions={PAGE_SIZE_OPTIONS} onPageSizeChange={(size) => { setPageSize(size); setPage(1) }} onChange={setPage} ariaLabel={t('assetLibraryPage.pagination')} />
      </>}
      </>}
      {selectedAsset && <AssetDetailDialog asset={selectedAsset} audioUrl={selectedAsset.media_kind !== 'audio' ? previewAudio(selectedAsset) : undefined} onClose={() => setSelectedAsset(null)} actions={selectedAsset.project_url ? <Button to={selectedAsset.project_url} variant="dark">{t('assetLibraryPage.openSourceProject')}</Button> : undefined} />}
    </div>
  </AppShell>
}
