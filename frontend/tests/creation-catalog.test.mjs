import assert from 'node:assert/strict'
import test from 'node:test'
import {
  CREATION_TEMPLATE_IDS,
  creationTemplateId,
  curatedCreationTemplates,
} from '../src/lib/creationTemplates.ts'
import { creationApiErrorDetail } from '../src/lib/creationApiError.ts'

test('旧服务端返回全部模板时，新创作只展示固定顺序的四个非真人模板', () => {
  const legacy = [
    { id: 'real_person', name: '真人街访', preview_cover: '/real.png' },
    { id: 'kids_flat', name: '儿童绘本扁平', default_ratio: '16:9' },
    { id: 'ink_guofeng', name: '水墨国风' },
    { id: 'soft_anime', name: '柔光动漫' },
    { id: 'anim_3d', name: '旧动画名称', preview_cover: '/3d.png', seedream_config: { strength: 0.8 } },
    { id: 'photorealistic', name: '写实摄影' },
    { id: 'anim_3d', name: '旧动画名称', preview_cover: '/3d.png', seedream_config: { strength: 0.8 } },
  ]
  const before = structuredClone(legacy)
  const curated = curatedCreationTemplates(legacy)
  assert.deepEqual(curated.map((item) => item.id), CREATION_TEMPLATE_IDS)
  assert.deepEqual(curated.map((item) => item.name), ['3D 动画', '二维动漫', '绘本插画', '国风水墨'])
  assert.ok(curated.every((item) => item.default_ratio === '9:16'))
  assert.equal(curated[0].preview_cover, '/3d.png')
  assert.deepEqual(curated[0].seedream_config, { strength: 0.8 })
  assert.deepEqual(legacy, before)
})

test('精选模板未返回时不伪造可用素材，旧画风链接回到默认3D', () => {
  assert.deepEqual(curatedCreationTemplates([]), [])
  assert.deepEqual(curatedCreationTemplates([{ id: 'real_person' }]), [])
  assert.deepEqual(curatedCreationTemplates([{ id: 'ink_guofeng' }]).map((item) => item.id), ['ink_guofeng'])
  assert.equal(creationTemplateId('real_person'), 'anim_3d')
  assert.equal(creationTemplateId(null), 'anim_3d')
  assert.equal(creationTemplateId('ink_guofeng'), 'ink_guofeng')
})

test('接口404与会话404分别说明原因，余额不足和其他业务错误保留原详情', () => {
  const endpointError = creationApiErrorDetail(404, 'Not Found')
  const sessionError = creationApiErrorDetail(404, '创作会话不存在')
  assert.match(endpointError, /接口尚未加载/)
  assert.match(sessionError, /创作会话不存在/)
  assert.doesNotMatch(sessionError, /重启/)
  const billing = { code: 'insufficient_balance', message: '余额不足' }
  assert.equal(creationApiErrorDetail(402, billing), billing)
  assert.equal(creationApiErrorDetail(404, '关联项目不存在'), '关联项目不存在')
  assert.equal(creationApiErrorDetail(503, '服务暂不可用'), '服务暂不可用')
})
