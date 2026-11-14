import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { ecomFormCategories, ecomReplanPath, ecomSetTotalFen, newlyAddedModelId, reconcileColorIndex } from '../src/lib/ecomSet.ts'
import { lookupMessage } from '../src/i18n/lookup.ts'
import { zhEcomSet } from '../src/i18n/locales/zh/ecomSet.ts'
import { enEcomSet } from '../src/i18n/locales/en/ecomSet.ts'
import { zhEcomHub } from '../src/i18n/locales/zh/ecomHub.ts'
import { enEcomHub } from '../src/i18n/locales/en/ecomHub.ts'

test('全免费套图的总价是 0，不回退成单价乘张数', () => {
  assert.equal(ecomSetTotalFen({ total_fen: 0, image_fen: 50, items: [{}, {}] }), 0)
  assert.equal(ecomSetTotalFen({ total_fen: 100, image_fen: 50, items: [{}, {}, {}] }), 100)
})

test('已有默认模特时，上传或生成后选中新模特而不是列表首位的默认模特', () => {
  const before = [{ id: 5 }, { id: 3 }]
  assert.equal(newlyAddedModelId(before, [{ id: 5 }, { id: 9 }, { id: 3 }]), 9)
  assert.equal(newlyAddedModelId([], [{ id: 9 }]), 9)
  assert.equal(newlyAddedModelId(before, before), 5)
  assert.equal(newlyAddedModelId([], []), null)
})

test('套图表单只展示实际支持的品类', () => {
  const options = ['general', 'scarf', 'apparel', 'jewelry', 'bag'].map((id) => ({ id, name: id }))
  assert.deepEqual(ecomFormCategories(options).map((item) => item.id), ['general', 'scarf'])
  assert.deepEqual(ecomFormCategories(undefined), [])
})

test('重新规划保留原品类：商品库套图回电商图并带上商品与颜色款', () => {
  assert.equal(ecomReplanPath({ category: 'apparel', styled: { product_id: 7, color_index: 2 } }), '/ecom?product=7&color=2')
  assert.equal(ecomReplanPath({ category: 'scarf', styled: { product_id: 7 } }), '/ecom?product=7&color=0')
  assert.equal(ecomReplanPath({ category: 'bag', styled: { product_id: null } }), '/ecom')
  assert.equal(ecomReplanPath({ category: 'scarf', styled: { product_id: null } }), null)
  assert.equal(ecomReplanPath({ category: 'general', styled: null }), null)
})

test('颜色款增删后按原图找回当前颜色，原颜色已删除时回到第一个', () => {
  const colors = [{ image_url: '/a' }, { image_url: '/b' }, { image_url: '/c' }]
  assert.equal(reconcileColorIndex(colors, 2, [{ image_url: '/b' }, { image_url: '/c' }]), 1)
  assert.equal(reconcileColorIndex(colors, 0, [{ image_url: '/b' }, { image_url: '/c' }]), 0)
  assert.equal(reconcileColorIndex(colors, 1, colors), 1)
})

test('套图组件引用的 ecomSet 文案键在中英文里都存在，且不引用其他命名空间的缺失键', () => {
  const folder = new URL('../src/components/ecomSet/', import.meta.url)
  const keys = new Set()
  for (const name of readdirSync(folder).filter((file) => file.endsWith('.tsx'))) {
    const source = readFileSync(new URL(name, folder), 'utf8')
    for (const match of source.matchAll(/\bt\(\s*'([\w.]+)'/g)) keys.add(match[1])
  }
  assert.ok(keys.has('ecomSet.result.downloadImage'))
  // 套图组件只用 ecomSet 与电商工作台 ecomHub（精修来源等）两个命名空间
  const catalogs = { ecomSet: [zhEcomSet, enEcomSet], ecomHub: [zhEcomHub, enEcomHub] }
  for (const key of keys) {
    const pair = catalogs[key.split('.')[0]]
    assert.ok(pair, `未知命名空间 ${key}`)
    assert.equal(typeof lookupMessage(pair[0], key), 'string', `zh 缺 ${key}`)
    assert.equal(typeof lookupMessage(pair[1], key), 'string', `en 缺 ${key}`)
  }
})
