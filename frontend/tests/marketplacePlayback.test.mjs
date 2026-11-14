import test from 'node:test'
import assert from 'node:assert/strict'
import { isFreeWork, isFreeEpisode, previewStatusLabel } from '../src/features/marketplace/playbackPolicy.ts'

test('免费公开由收费类型决定，零价版权咨询仍为短试看', () => {
  assert.equal(isFreeWork({ sale_type: 'free', price_fen: 0 }), true)
  assert.equal(isFreeWork({ sale_type: 'copyright_contact', price_fen: 0 }), false)
  assert.equal(isFreeWork({ sale_type: 'platform_unlock', price_fen: 0 }), false)
  assert.equal(isFreeWork({ price_fen: 0 }), false)
})

test('全免费系列和收费系列前N集免费，不影响后续收费分集', () => {
  assert.equal(isFreeEpisode({ sale_type: 'free', free_episode_count: 0 }, 3), true)
  assert.equal(isFreeEpisode({ sale_type: 'platform_unlock', free_episode_count: 2 }, 2), true)
  assert.equal(isFreeEpisode({ sale_type: 'platform_unlock', free_episode_count: 2 }, 3), false)
  assert.equal(isFreeEpisode({ sale_type: 'copyright_contact', free_episode_count: 0 }, 1), false)
})

test('免费无需试看，收费不能把none状态显示成就绪', () => {
  assert.equal(previewStatusLabel('none', true), '无需试看')
  assert.equal(previewStatusLabel('failed', true), '无需试看')
  assert.equal(previewStatusLabel('none', false), '未生成')
  assert.equal(previewStatusLabel('running', false), '正在生成短试看')
  assert.equal(previewStatusLabel('failed', false), '生成失败，可重试')
})
