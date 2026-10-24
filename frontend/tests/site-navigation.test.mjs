import assert from 'node:assert/strict'
import test from 'node:test'
import { CREATION_NAV, RESOURCE_NAV, resolveNavActive } from '../src/lib/siteNavigation.ts'

test('自由画布是独立主导航入口，所有入口路径唯一', () => {
  const entries = [...CREATION_NAV, ...RESOURCE_NAV]
  assert.equal(CREATION_NAV.find((item) => item.key === 'canvas')?.path, '/canvas')
  assert.equal(new Set(entries.map((item) => item.path)).size, entries.length)
  assert.equal(resolveNavActive('/canvas'), 'canvas')
  assert.equal(resolveNavActive('/canvas-unknown'), undefined)
})

test('资产、团队和旧短视频地址高亮对应入口，不被漫剧前缀覆盖', () => {
  for (const [path, expected] of [
    ['/drama/assets', 'assets'], ['/drama/projects/422', 'drama'],
    ['/teams/12', 'team'], ['/team-invitations', 'team'],
    ['/studio/3/style', 'kepu'], ['/templates', 'kepu'], ['/history', 'kepu'],
    ['/tools/t2i', 'tools'], ['/settings', 'settings'], ['/help', 'help'],
  ]) assert.equal(resolveNavActive(path), expected)
  assert.equal(resolveNavActive('/studio/3', 'studio'), 'kepu')
  assert.equal(resolveNavActive('/drama', 'canvas'), 'canvas')
})
