import assert from 'node:assert/strict'
import { test } from 'node:test'
import { applyComplianceSuggestion } from '../src/lib/complianceChanges.ts'

test('emoji和重复词按原稿码点仅替换目标，替换后旧报告不能改下一处', () => {
  const text = '😀最好用，最好用'
  const hit = { word: '最好用', category: '最高级', level: 'block', start: 5, end: 8, suggestion: '好用' }
  assert.equal(applyComplianceSuggestion(text, text, hit), '😀最好用，好用')
  assert.throws(() => applyComplianceSuggestion('😀新输入，最好用', text, hit), /重新检查/)
  const first = { ...hit, start: 1, end: 4 }
  const changed = applyComplianceSuggestion(text, text, first)
  assert.throws(() => applyComplianceSuggestion(changed, text, hit), /重新检查/)
  assert.throws(() => applyComplianceSuggestion(text, text, { ...hit, suggestion: null }), /重新检查/)
})

test('明确删除与CRLF说话人前缀保持原样', () => {
  const text = '@duration:8\r\n【对白】角色：😀全网第一\r\n'
  const start = Array.from(text).indexOf('全')
  const hit = { word: '全网第一', start, end: start + 4, suggestion: '' }
  assert.equal(applyComplianceSuggestion(text, text, hit), '@duration:8\r\n【对白】角色：😀\r\n')
})
