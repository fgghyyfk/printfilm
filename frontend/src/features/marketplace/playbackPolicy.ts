/** 与后端播放策略保持一致；参考价为零不能替代明确的免费类型。 */
export function isFreeWork(item: { sale_type?: string }) {
  return item.sale_type === 'free'
}

export function isFreeEpisode(series: { sale_type?: string; free_episode_count?: number }, episodeNo: number) {
  return isFreeWork(series) || (episodeNo > 0 && episodeNo <= (series.free_episode_count || 0))
}

export function previewStatusLabel(status: string | undefined, free: boolean) {
  if (free) return '无需试看'
  const labels: Record<string, string> = {
    none: '未生成', missing: '未生成', pending: '排队中', running: '正在生成短试看',
    done: '短试看已就绪', failed: '生成失败，可重试', cancelled: '任务已取消',
  }
  return labels[status || 'none'] || status
}
