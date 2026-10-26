import type { ToolRunRecord } from '../api/tools.ts'
import type { AssetDetailRecord } from './assetDetails.ts'

/** 将独立生成记录适配为全局资产详情，预览和下载始终使用所选正式结果。 */
export function toolRunDetailAsset(record: ToolRunRecord, resultIndex = 0): AssetDetailRecord {
  // 原始提示词与参数来自已提交的任务，不能使用当前生成表单覆盖。
  const params = record.params || {}
  const url = record.kind === 'text' ? null : record.urls[resultIndex] || record.urls[0] || null
  const duration = Number(String(params.duration || params.duration_sec || '').replace(/s$/i, ''))
  const labels: Record<string, string> = { video: '视频生成', image: '图片生成', audio: '音频生成', text: '文字生成' }
  const statuses: Record<string, string> = { queued: '任务正在排队，结果会自动更新。', running: '任务正在生成，结果会自动更新。', succeeded: '任务已完成，暂无可预览的媒体结果。', failed: '生成失败。', cancelled: '任务已取消。' }
  const textContent = record.text_content || record.error || statuses[record.status] || `任务状态：${record.status}`

  return {
    id: record.id,
    name: `${labels[record.kind] || '生成记录'} #${record.id}`,
    type: record.kind === 'text' || !url ? 'text' : record.kind,
    url,
    // 视频首帧只作已有视频的 poster；尚无成片时不能把图片作为视频下载或播放。
    cover: record.kind === 'image' ? url || record.preview_url
      : record.kind === 'video' && url && record.preview_url !== url ? record.preview_url : null,
    updated_at: record.finished_at || record.created_at,
    params: {
      ...((record.kind === 'text' || !url) ? { textContent } : {}),
      media_metadata: {
        source: 'generated',
        model_id: params.model_id || params.model,
        prompt: record.prompt,
        created_at: record.finished_at || record.created_at,
        parameters: {
          aspect_ratio: params.ratio || params.aspect_ratio,
          resolution: params.resolution,
          ...(record.kind === 'video' && Number.isFinite(duration) && duration > 0 ? { duration_sec: duration } : {}),
          ...(record.kind === 'audio' && params.voice ? { speaker: params.voice } : {}),
        },
      },
    },
  }
}
