import { translate as translateUi } from '../i18n/runtime'
/** 浏览器内无损拼接本集 MP4；编码不一致时回退服务端统一重编码 */

import type { DramaFragment } from '../api/drama'
import { dramaApi, resolveDramaMediaUrl } from '../api/drama'
import { tasksApi } from '../api/tasks'
import { fetchMediaBlob } from './clientDownload'
import { sanitizeMediaBasename } from './canvasNodeMedia'

export type EpisodeComposeClip = {
  id: number
  label: string
  url: string
}

export type EpisodeComposeProgress = {
  phase: 'download' | 'concat' | 'server'
  done: number
  total: number
}

/** 按分镜顺序收集可拼接的视频地址 */
export function listEpisodeComposeClips(fragments: DramaFragment[]): EpisodeComposeClip[] {
  const clips: EpisodeComposeClip[] = []
  fragments.forEach((fragment, index) => {
    const url = resolveDramaMediaUrl(fragment.video)
    if (!url || fragment.id == null) return
    clips.push({
      id: fragment.id,
      label: translateUi("workflow.shot", { v1: String(index + 1) }),
      url,
    })
  })
  return clips
}

/** 全片下载文件名 */
export function episodeComposeFilename(episodeName: string) {
  return `${sanitizeMediaBasename(episodeName || '本集')}_全片.mp4`
}

/** 服务端统一重编码拼接并拉回 Blob */
async function composeEpisodeVideoServer(
  episodeId: number,
  clips: EpisodeComposeClip[],
  onProgress?: (progress: EpisodeComposeProgress) => void,
): Promise<Blob> {
  onProgress?.({ phase: 'server', done: 0, total: clips.length })
  const queued = await dramaApi.composeEpisode(
    episodeId,
    clips.map((c) => c.id),
  )
  // 本地FFmpeg任务不可协作取消，沿用任务状态回读，不重复提交或假称已停止。
  let task = await tasksApi.get(queued.task_id)
  while (!['succeeded', 'failed', 'cancelled', 'awaiting_review'].includes(task.status)) {
    onProgress?.({ phase: 'server', done: 0, total: clips.length })
    await new Promise<void>((resolve) => setTimeout(resolve, 1500))
    task = await tasksApi.get(queued.task_id)
  }
  if (task.status !== 'succeeded') throw new Error(task.error_message || translateUi("workflow.videoCompositionDidNotCompleteCheckTheTaskRecord"))
  const url = resolveDramaMediaUrl(String(task.result_payload?.video_url || ''))
  if (!url) throw new Error(translateUi("workflow.serverCompositionReturnedNoDownloadUrl"))
  const blob = await fetchMediaBlob(url)
  onProgress?.({ phase: 'server', done: clips.length, total: clips.length })
  return blob
}

/** 拉取各镜并在本地拼成一条 MP4；无损失败则自动走服务端 */
export async function composeEpisodeVideoClient(
  clips: EpisodeComposeClip[],
  onProgress?: (progress: EpisodeComposeProgress) => void,
  options?: { episodeId?: number },
): Promise<Blob> {
  if (clips.length === 0) {
    throw new Error(translateUi("workflow.thisEpisodeHasNoClipsReadyToCompose"))
  }

  const buffers: Uint8Array[] = new Array(clips.length)
  let downloaded = 0
  await Promise.all(
    clips.map(async (clip, index) => {
      const blob = await fetchMediaBlob(clip.url)
      buffers[index] = new Uint8Array(await blob.arrayBuffer())
      downloaded += 1
      onProgress?.({ phase: 'download', done: downloaded, total: clips.length })
    }),
  )

  if (clips.length === 1) {
    const single = buffers[0]
    return new Blob([copyToArrayBuffer(single)], { type: 'video/mp4' })
  }

  onProgress?.({ phase: 'concat', done: 0, total: clips.length })
  const { concatMp4, isMp4, mp4Compat } = await import('mp4cat')
  const names = clips.map((clip) => clip.label)
  for (let i = 0; i < buffers.length; i++) {
    if (!isMp4(buffers[i])) {
      throw new Error(translateUi("workflow.isNotAComposableMp4", { v1: String(names[i]) }))
    }
  }
  const compat = mp4Compat(buffers, { names })
  if (!compat.ok) {
    const episodeId = options?.episodeId
    if (episodeId != null && episodeId > 0) {
      // Seedance 各镜 HEVC SPS/PPS 常不一致，服务端统一重编码即可
      return composeEpisodeVideoServer(episodeId, clips, onProgress)
    }
    throw new Error(
      translateUi("workflow.theClipsUseDifferentCodecsAndCannotBeJoined", { v1: String(compat.reason ? `（${compat.reason}）` : '') }),
    )
  }
  const merged = concatMp4(buffers)
  onProgress?.({ phase: 'concat', done: clips.length, total: clips.length })
  return new Blob([copyToArrayBuffer(merged)], { type: 'video/mp4' })
}

// 拷成独立 ArrayBuffer，避免 Uint8Array 视图在 TS 里不能当 BlobPart
function copyToArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const copy = new ArrayBuffer(bytes.byteLength)
  new Uint8Array(copy).set(bytes)
  return copy
}
