import type { DramaEpisode, DramaFragment } from '../api/drama'
import { mergeFragmentRuntime, type FragmentEditSnapshot } from './dramaFragmentEditing.ts'

type Draft = DramaFragment & { clientKey?: string }
type RowsUpdate = Draft[] | ((rows: Draft[]) => Draft[])
export type FragmentSaveState = { dirty: boolean; saving: boolean; conflict: boolean }
type Options = {
  persist: (rows: Draft[], snapshot: FragmentEditSnapshot) => Promise<DramaEpisode>
  onRows: (rows: Draft[]) => void
  onSaved: (episode: DramaEpisode) => void
  onState: (state: FragmentSaveState) => void
  onError: (error: unknown) => void
  delay?: number
}

/** 页面本地创作 epoch 与服务端 revision 分开；所有保存共享一个串行请求。 */
export function createFragmentAutosave(options: Options) {
  let rows: Draft[] = []
  let revision = 0
  let epoch = 0
  let savedEpoch = 0
  let serial = 0
  let loadVersion = 0
  let loaded = false
  let active = true
  let conflict = false
  let deletedIds = new Set<number>()
  let timer: ReturnType<typeof setTimeout> | undefined
  let inFlight: Promise<DramaEpisode> | null = null
  let latestEpisode: DramaEpisode | null = null
  let holds = 0
  let releaseHold: (() => void) | undefined
  let holdPromise: Promise<void> | undefined

  const keyed = (values: Draft[]) => values.map((row) => ({ ...row, clientKey: row.clientKey || `draft-${++serial}` }))
  const state = () => ({ dirty: epoch !== savedEpoch, saving: inFlight !== null, conflict })
  const notify = () => options.onState(state())
  const publish = () => { options.onRows(rows); notify() }
  const cancelTimer = () => { if (timer) clearTimeout(timer); timer = undefined }
  const schedule = () => {
    cancelTimer()
    if (!active || !loaded || conflict || holds || epoch === savedEpoch || inFlight) return
    timer = setTimeout(() => { void flush().catch(() => undefined) }, options.delay ?? 900)
  }
  const checkActive = () => { if (!active) throw new Error('分集已切换，本次结果不再应用') }

  function load(episode: DramaEpisode) {
    if (!active) return
    loadVersion += 1
    cancelTimer()
    latestEpisode = episode
    rows = keyed(episode.fragments || [])
    revision = episode.revision
    epoch = savedEpoch = 0
    deletedIds = new Set()
    loaded = true
    conflict = false
    publish()
  }

  function edit(update: RowsUpdate) {
    if (!active) return
    const next = keyed(typeof update === 'function' ? update(rows) : update)
    const remaining = new Set<string | undefined>(next.map((row) => row.clientKey))
    for (const row of rows) if (row.id > 0 && !remaining.has(row.clientKey)) deletedIds.add(row.id)
    rows = next
    epoch += 1
    publish()
    schedule()
  }

  function runtime(update: RowsUpdate) {
    if (!active) return
    rows = typeof update === 'function' ? update(rows) : update
    options.onRows(rows)
  }

  async function drain() {
    while (true) {
      if (holds) await holdPromise
      checkActive()
      if (epoch === savedEpoch) return latestEpisode!
      const sentVersion = loadVersion
      const sentEpoch = epoch
      const sent = JSON.parse(JSON.stringify(rows)) as Draft[]
      const sentDeletes = [...deletedIds]
      const result = await options.persist(sent, { revision, deletedIds: sentDeletes })
      checkActive()
      if (loadVersion !== sentVersion) throw Object.assign(new Error('已加载新版分镜'), { obsolete: true })
      const returned = result.fragments || []
      const idsByKey = new Map<string | undefined, number>()
      sent.forEach((row, index) => {
        const remote = row.id > 0 ? returned.find((item) => item.id === row.id)
          : returned.find((item) => item.sort_order === index)
        if (remote) idsByKey.set(row.clientKey, remote.id)
      })
      sentDeletes.forEach((id) => deletedIds.delete(id))
      const currentKeys = new Set(rows.map((row) => row.clientKey))
      for (const row of sent) {
        const assigned = idsByKey.get(row.clientKey)
        if (assigned && !currentKeys.has(row.clientKey)) deletedIds.add(assigned)
      }
      rows = rows.map((row) => ({ ...row, id: idsByKey.get(row.clientKey) || row.id }))
      revision = result.revision
      latestEpisode = result
      savedEpoch = sentEpoch
      if (epoch === sentEpoch) {
        const keysById = new Map(rows.map((row) => [row.id, row.clientKey]))
        rows = keyed(returned.map((row) => ({ ...row, clientKey: keysById.get(row.id) })))
      } else {
        rows = mergeFragmentRuntime(rows, returned)
      }
      options.onSaved(result)
      publish()
    }
  }

  function flush(): Promise<DramaEpisode> {
    cancelTimer()
    if (!active || !loaded) return Promise.reject(new Error('请先加载分镜'))
    if (conflict) return Promise.reject(new Error('内容已在别处更新，请刷新；本地稿已保留'))
    if (inFlight) return inFlight
    const request = drain().catch((error: unknown) => {
      if (active && !(error as { obsolete?: boolean })?.obsolete) {
        conflict = (error as { status?: number })?.status === 409
        options.onError(error)
      }
      throw error
    }).finally(() => {
      inFlight = null
      if (active) notify()
    })
    inFlight = request
    notify()
    return request
  }

  return {
    load, edit, runtime, flush, state,
    rows: () => rows,
    epoch: () => epoch,
    isActive: () => active,
    /** 设置请求等待期间不发旧的内容快照；解除后仍由同一 flush 保存最新稿。 */
    hold() {
      cancelTimer()
      if (holds++ === 0) holdPromise = new Promise<void>((resolve) => { releaseHold = resolve })
      let released = false
      return () => {
        if (released) return
        released = true
        if (--holds === 0) { releaseHold?.(); releaseHold = undefined; schedule() }
      }
    },
    activate() { active = true },
    dispose() { active = false; cancelTimer(); releaseHold?.() },
  }
}
