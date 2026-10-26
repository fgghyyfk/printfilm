import test from 'node:test'
import assert from 'node:assert/strict'
import { assetDetailInfo, formatMediaDuration } from '../src/lib/assetDetails.ts'

test('上传媒体显示上传，隐藏残留的旧模型与生成参数', () => {
  const value = assetDetailInfo({ type: 'character', params: { model_id: 'old', generation: { source: 'upload', resolution: '4K' } } })
  assert.equal(value.source, '上传')
  assert.equal(value.model, '')
  assert.deepEqual(value.parameters, [])
})

test('当前媒体快照优先于下次生成的表单参数', () => {
  const value = assetDetailInfo({ type: 'video', params: { model_id: 'new-form', resolution: '4K', media_metadata: { source: 'generated', model_id: 'actual-model', prompt: '完整提示词'.repeat(500), parameters: { resolution: '480p', generate_audio: false, seed: 0 } } } })
  assert.equal(value.model, 'actual-model')
  assert.equal(value.prompt.length, 2500)
  assert.deepEqual(value.parameters.map(item => item.value), ['480p', '否', '0'])
})

test('未记录的来源和模型不伪造生成信息', () => {
  const value = assetDetailInfo({ type: 'image', params: { media_metadata: { source: 'unknown' }, model_id: 'stale' } })
  assert.equal(value.source, '未记录')
  assert.equal(value.model, '')
})

test('媒体时长只接受正的有限实测值，旧时长字符串不重复拼单位', () => {
  assert.equal(formatMediaDuration(8), '8 秒')
  assert.equal(formatMediaDuration(8.125), '8.13 秒')
  for (const value of [0, -1, Infinity, NaN, '8', true, null]) assert.equal(formatMediaDuration(value), '')
  const info = assetDetailInfo({ type: 'video', params: { media_metadata: { source: 'generated', parameters: { duration_sec: '8s', generate_audio: 'false', seed: 0 } } } })
  assert.deepEqual(info.parameters.map((item) => item.value), ['8 秒', '否', '0'])
})
