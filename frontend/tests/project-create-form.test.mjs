import assert from 'node:assert/strict'
import test from 'node:test'
import { validateProjectCreate } from '../src/lib/projectCreateForm.ts'

const draft = (overrides = {}) => ({ title: '', source: '一个年轻的邮差在雨夜收到一封写给未来自己的信。', description: '', episodeCount: '12', imageStyleId: '', ...overrides })

test('personal drama accepts an optional name and trims fields without changing episode count', () => {
  const { values, errors } = validateProjectCreate(draft({ source: `  ${draft().source}  `, description: '  一封来信  ', episodeCount: '120' }), { kind: 'drama' })
  assert.deepEqual(errors, {})
  assert.equal(values.title, '')
  assert.equal(values.source, draft().source)
  assert.equal(values.description, '一封来信')
  assert.equal(values.episodeCount, 120)
})

test('invalid or fractional episode counts are rejected instead of silently clamped', () => {
  for (const episodeCount of ['', '0', '121', '1.5', '-1', '1e2', 'Infinity']) {
    assert.ok(validateProjectCreate(draft({ episodeCount }), { kind: 'drama' }).errors.episodeCount, episodeCount)
  }
})

test('story boundaries and whitespace are validated', () => {
  for (const source of ['', ' '.repeat(20), '字'.repeat(19), '字'.repeat(2001)]) {
    assert.ok(validateProjectCreate(draft({ source }), { kind: 'drama' }).errors.source)
  }
  for (const source of ['字'.repeat(20), '字'.repeat(2000)]) {
    assert.equal(validateProjectCreate(draft({ source }), { kind: 'drama' }).errors.source, undefined)
  }
})

test('canvas requires a name and ignores hidden story, episodes, and style controls', () => {
  assert.ok(validateProjectCreate(draft({ title: ' ' }), { kind: 'canvas' }).errors.title)
  const { values, errors } = validateProjectCreate(draft({ title: '  未命名画布 ', source: '', episodeCount: 'invalid', imageStyleId: 'pixel-art' }), { kind: 'canvas' })
  assert.deepEqual(errors, {})
  assert.equal(values.title, '未命名画布')
  assert.equal(values.source, '')
  assert.equal(values.episodeCount, 1)
  assert.equal(values.imageStyleId, '')
})

test('team constraints preserve long scripts, while requiring a project title', () => {
  const constraints = { kind: 'drama', titleRequired: true, titleMaxLength: 60, sourceMaxLength: 100000 }
  assert.ok(validateProjectCreate(draft(), constraints).errors.title)
  assert.ok(validateProjectCreate(draft({ title: '字'.repeat(61) }), constraints).errors.title)
  assert.deepEqual(validateProjectCreate(draft({ title: '雨夜来信', source: '字'.repeat(100000) }), constraints).errors, {})
  assert.ok(validateProjectCreate(draft({ title: '雨夜来信', source: '字'.repeat(100001) }), constraints).errors.source)
  assert.ok(validateProjectCreate(draft({ title: '雨夜来信', description: '字'.repeat(501) }), constraints).errors.description)
})
