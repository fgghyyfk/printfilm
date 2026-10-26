import assert from 'node:assert/strict'
import test from 'node:test'
import { IMAGE_TEXT_STEPS, imageTextAssetReadiness, imageTextBoardStep, imageTextNextAction } from '../src/lib/imageTextStoryboard.ts'

const shot = (image_url = null, audio_url = null) => ({ image_url, audio_url })

test('image-text creation has four manual checkpoints without a video generation stage', () => {
  assert.deepEqual(IMAGE_TEXT_STEPS.map(({ label }) => label), ['创作设置', '文案与分镜', '画面与配音', '成片预览'])
})

test('generated script waits for confirmation before beginning assets', () => {
  const project = { status: 'SCRIPT_READY', progress: 15, shots: [shot(), shot()] }
  assert.equal(imageTextBoardStep(project), 1)
  assert.equal(imageTextNextAction(project), 'assets')
  assert.equal(imageTextBoardStep({ ...project, status: 'IMAGING', progress: 18 }), 2)
})

test('complete images alone cannot enable composition without voiceover', () => {
  const project = { status: 'FAILED', progress: 40, shots: [shot('/1.png', '/voice.mp3'), shot('/2.png')] }
  assert.deepEqual(imageTextAssetReadiness(project), { total: 2, images: 2, audio: 1, ready: false })
  assert.equal(imageTextNextAction(project), 'assets')
  assert.equal(imageTextBoardStep(project), 2)
})

test('completed assets remain available for review until the user starts composition', () => {
  const project = { status: 'IMAGE_READY', shots: [shot('/1.png', '/voice.mp3')] }
  assert.equal(imageTextBoardStep(project), 2)
  assert.equal(imageTextNextAction(project), 'compose')
  assert.equal(imageTextBoardStep({ ...project, status: 'COMPOSING' }), 3)
})

test('cancellation or failures resume the available step and preserve successful assets', () => {
  assert.equal(imageTextNextAction({ status: 'FAILED', shots: [] }), 'script')
  assert.equal(imageTextBoardStep({ status: 'CANCELLED', progress: 18, shots: [shot()] }), 2)
  assert.equal(imageTextNextAction({ status: 'CANCELLED', shots: [shot('/1.png', '/voice.mp3')] }), 'compose')
  assert.equal(imageTextAssetReadiness({ status: 'DRAFT' }).ready, false)
})

test('finished films open preview and remain accessible while reviewing earlier stages', () => {
  const project = { status: 'DONE', shots: [shot('/1.png', '/voice.mp3')], final_video_url: '/movie.mp4' }
  assert.equal(imageTextBoardStep(project), 3)
  assert.equal(imageTextNextAction(project), 'preview')
})
