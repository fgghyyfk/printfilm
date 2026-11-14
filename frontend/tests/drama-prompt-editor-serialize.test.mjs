import assert from 'node:assert/strict'
import test from 'node:test'
import { loadSource } from './bundleModule.mjs'

globalThis.Node = { TEXT_NODE: 3, ELEMENT_NODE: 1 }
const { serializePromptEditorContent } = await loadSource('../src/lib/dramaEpisodePromptEditor.ts')

// 只实现序列化用到的 DOM 字段，保留真实的遍历逻辑
const text = (textContent) => ({ nodeType: 3, textContent, parentElement: { closest: () => null } })
const element = (tagName, children = [], dataset = {}) => ({
  nodeType: 1, tagName, dataset, childNodes: children, closest: () => null,
})
const br = () => element('BR')
const duration = (sec) => element('SPAN', [], { mention: 'true', durationSec: String(sec) })
const asset = (id) => element('SPAN', [], { mention: 'true', assetId: String(id) })
const serialize = (...children) => serializePromptEditorContent(element('DIV', children))

test('紧跟时长标签输入：文字另起一行', () => {
  assert.equal(serialize(duration(3), text('我的字'), br(), text('第一场')), '@duration:3\n我的字\n第一场')
})

test('插入标签自带的空格不进正文', () => {
  assert.equal(serialize(duration(3), text(' 我的字')), '@duration:3\n我的字')
})

test('只有自带空格时不产生多余空行', () => {
  assert.equal(serialize(duration(3), text(' ')), '@duration:3')
  assert.equal(serialize(duration(3), text(' '), br(), text('正文')), '@duration:3\n正文')
})

test('标签后已有换行时不重复换行', () => {
  assert.equal(serialize(duration(3), br(), text('正文')), '@duration:3\n正文')
  assert.equal(serialize(duration(3), text('\n正文')), '@duration:3\n正文')
})

test('连续两个标签各占一行', () => {
  assert.equal(serialize(duration(3), duration(2), text(' 文字')), '@duration:3\n@duration:2\n文字')
})

test('标签后紧跟资产引用也另起一行', () => {
  assert.equal(serialize(duration(3), asset(5), text(' 走进来')), '@duration:3\n@asset:5 走进来')
})

test('块级元素里的内容不多加空行', () => {
  assert.equal(serialize(duration(3), element('DIV', [text('下一行')])), '@duration:3\n下一行')
})

test('标签前的文字和普通内容不受影响', () => {
  assert.equal(serialize(text('前文'), duration(3)), '前文@duration:3')
  assert.equal(serialize(text('甲 '), asset(1), text(' 乙')), '甲 @asset:1 乙')
})
