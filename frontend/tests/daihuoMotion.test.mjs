import { loadSource } from './bundleModule.mjs'
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { zhDaihuo } from '../src/i18n/locales/zh/daihuo.ts'
import { enDaihuo } from '../src/i18n/locales/en/daihuo.ts'

const motion = await loadSource('../src/lib/daihuoMotion.ts')
const status = await loadSource('../src/lib/status.ts')

// 带货识别、静图判定与动作回显
test('daihuo helpers', () => {
  assert.equal(motion.isDaihuoProject({ template_id: 'daihuo_product' }), true)
  assert.equal(motion.isDaihuoProject({ template_id: 'kepu', ecom_set_id: 3 }), true)
  assert.equal(motion.isDaihuoProject({ template_id: 'kepu' }), false)
  assert.equal(motion.isStaticShot('image_text', {}), true)
  assert.equal(motion.isStaticShot('full', { video_skip_reason: 'static' }), true)
  assert.equal(motion.isStaticShot('full', { video_skip_reason: null }), false)
  assert.equal(motion.motionActionOf('环绕展示'), 'orbit')
  assert.equal(motion.motionActionOf('slow push in'), null)
})

// 带货混合展示：要后期配音；静图镜头不等视频；科普 full 不变
test('daihuo full phase needs narration and skips static shots', () => {
  const shot = (extra) => ({ image_url: '/a.jpg', audio_url: '/a.mp3', video_url: null, ...extra })
  const daihuo = { pipeline_mode: 'full', template_id: 'daihuo_product' }
  assert.equal(status.kepuBillingPhase({ ...daihuo, shots: [shot({ audio_url: null })] }), 'assets')
  assert.equal(status.kepuBillingPhase({ ...daihuo, shots: [shot({ video_skip_reason: 'static' }), shot()] }), 'videos')
  assert.equal(status.kepuBillingPhase({ ...daihuo, shots: [shot({ video_skip_reason: 'static' }), shot({ video_url: '/v.mp4' })] }), 'compose')
  assert.equal(status.kepuBillingPhase({ pipeline_mode: 'full', template_id: 'kepu', shots: [shot({ audio_url: null })] }), 'videos')
  assert.equal(status.shotDisplayKind(shot({ video_skip_reason: 'static' }), { pipelineMode: 'full' }), 'image_ready')
})

// 新文案中英文结构一致且非空
test('daihuo motion translations match', () => {
  const walk = (zh, en, path) => {
    for (const key of Object.keys(zh)) {
      assert.ok(key in en, `${path}.${key} missing in en`)
      if (typeof zh[key] === 'object') walk(zh[key], en[key], `${path}.${key}`)
      else assert.ok(String(en[key]).trim(), `${path}.${key} empty`)
    }
  }
  walk(zhDaihuo, enDaihuo, 'daihuo')
  walk(enDaihuo, zhDaihuo, 'daihuo')
})
