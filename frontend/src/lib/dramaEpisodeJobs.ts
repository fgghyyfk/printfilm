/** 分集任务以集号独立记录，兼容个人历史的单槽状态字段。 */
export function readEpisodeJob(script: { params?: Record<string, unknown> | null } | null, number: number): { status: string; mode: string; error: string; assetsCreated: number } {
  const params = script?.params || {}
  const jobs = params.episode_jobs
  if (jobs && typeof jobs === 'object') {
    const job = (jobs as Record<string, Record<string, unknown>>)[String(number)] || {}
    return { status: String(job.status || ''), mode: String(job.mode || ''), error: String(job.error || ''), assetsCreated: Number(job.assets_created || 0) }
  }
  if (Number(params.episode_optimize_number) !== number) return { status: '', mode: '', error: '', assetsCreated: 0 }
  return { status: String(params.episode_optimize_status || ''), mode: String(params.episode_optimize_mode || ''), error: String(params.episode_optimize_error || ''), assetsCreated: Number(params.episode_optimize_assets_created || 0) }
}
