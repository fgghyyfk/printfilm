import assert from 'node:assert/strict'
import test from 'node:test'
import { HOME_NAV, CREATION_NAV, RESOURCE_NAV, PERSONAL_NAV, resolveNavActive } from '../src/lib/siteNavigation.ts'

test('自由画布是独立主导航入口，所有入口路径唯一', () => {
  const entries = [HOME_NAV, ...CREATION_NAV, ...RESOURCE_NAV, ...PERSONAL_NAV]
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
    ['/tools/video', 'video'], ['/tools/image', 'image'], ['/tools/audio', 'audio'],
    ['/settings', 'settings'], ['/help', 'help'],
  ]) assert.equal(resolveNavActive(path), expected)
  assert.equal(resolveNavActive('/studio/3', 'studio'), 'kepu')
  assert.equal(resolveNavActive('/drama', 'canvas'), 'canvas')
})

test('公开顶栏只展示核心产品，个人入口归入头像菜单', () => {
  assert.deepEqual(HOME_NAV, { key: 'home', path: '/', label: 'nav.home' })
  assert.deepEqual(CREATION_NAV.map((item) => item.key), ['drama', 'canvas', 'kepu'])
  assert.deepEqual(RESOURCE_NAV.map((item) => item.key), ['pricing'])
  assert.deepEqual(PERSONAL_NAV.map((item) => item.key), ['tasks', 'assets'])
  assert.equal(resolveNavActive('/tools'), undefined)
  assert.equal(resolveNavActive('/tools/t2i'), undefined)
  assert.equal(resolveNavActive('/'), 'home')
})
