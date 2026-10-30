import test from 'node:test'
import assert from 'node:assert/strict'
import { readEpisodeJob } from '../src/lib/dramaEpisodeJobs.ts'

test('并行单集任务状态互不覆盖，读取指定集号', () => {
  const script = { params: { episode_jobs: { 1: { status: 'generating', mode: 'body' }, 2: { status: 'completed' } }, episode_optimize_status: 'failed', episode_optimize_number: 2 } }
  assert.equal(readEpisodeJob(script, 1).status, 'generating')
  assert.equal(readEpisodeJob(script, 2).status, 'completed')
  assert.equal(readEpisodeJob(script, 3).status, '')
})

test('个人历史接口只在集号匹配时读取旧状态', () => {
  const script = { params: { episode_optimize_status: 'generating', episode_optimize_number: 8 } }
  assert.equal(readEpisodeJob(script, 8).status, 'generating')
  assert.equal(readEpisodeJob(script, 1).status, '')
})
