import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  batchRegenAudioPlan,
  canRegenShotAudio,
  kepuBillingPhase,
  shotImageAction,
} from '../src/lib/kepuShotActions.ts'

const ready = { image_url: '/i.png', audio_url: '/a.mp3', video_url: '/v.mp4' }

test('批量重配音只提交一次：数字人带上其余段，图文配音本来就覆盖整片', () => {
  assert.deepEqual(batchRegenAudioPlan({ pipeline_mode: 'avatar' }, [3, 1, 2]), { shotId: 3, extraShotIds: [1, 2] })
  assert.deepEqual(batchRegenAudioPlan({ pipeline_mode: 'image_text' }, [3, 1]), { shotId: 3, extraShotIds: [] })
  assert.equal(batchRegenAudioPlan({ pipeline_mode: 'avatar' }, []), null)
})

test('视频内置配音时没有可重配的配音', () => {
  assert.equal(canRegenShotAudio({ native_video_audio: true }), false)
  assert.equal(canRegenShotAudio({ native_video_audio: false }), true)
  assert.equal(batchRegenAudioPlan({ pipeline_mode: 'full', native_video_audio: true }, [1, 2]), null)
})

test('数字人重绘走重画场景，其它模式走普通生图', () => {
  assert.equal(shotImageAction('avatar'), 'avatar_scene')
  assert.equal(shotImageAction('full'), 'image')
  assert.equal(shotImageAction('image_text'), 'image')
})

test('阶段判定与后端一致：隐私跳过的镜头算视频已收敛', () => {
  const skipped = { ...ready, video_url: null, video_skip_reason: 'privacy' }
  assert.equal(kepuBillingPhase({ pipeline_mode: 'full', shots: [ready, skipped] }), 'compose')
  assert.equal(kepuBillingPhase({ pipeline_mode: 'full', shots: [ready, { ...skipped, video_skip_reason: null }] }), 'videos')
})

test('编辑器轮询任务状态、数字人走重画场景，并按活动任务锁按钮', () => {
  const source = readFileSync(new URL('../src/pages/studio/EditorPage.tsx', import.meta.url), 'utf8')
  assert.match(source, /isProjectBusy\(project\)/)
  assert.match(source, /setInterval/)
  assert.match(source, /api\.regenAvatarScene/)
  assert.match(source, /isShotGenerating\(project, shot\.id\)/)
  assert.match(source, /canRegenShotAudio\(project\)/)
})

test('分镜台批量配音只提交一次', () => {
  const source = readFileSync(new URL('../src/pages/studio/StoryboardPage.tsx', import.meta.url), 'utf8')
  assert.match(source, /batchRegenAudioPlan\(project, batchSelected\)/)
  assert.doesNotMatch(source, /await api\.regenAudio\(project\.id, shotId\)/)
})
