import assert from 'node:assert/strict'
import test from 'node:test'
import { validateProjectCreate } from '../src/lib/projectCreateForm.ts'

const draft = (overrides = {}) => ({ title: '', source: '一个年轻的邮差在雨夜收到一封写给未来自己的信。', description: '', imageStyleId: '', episodeCount: '1', ...overrides })

test('personal drama accepts an optional name, trims fields, and starts with one episode', () => {
  const { values, errors } = validateProjectCreate(draft({ source: `  ${draft().source}  `, description: '  一封来信  ' }), { kind: 'drama' })
  assert.deepEqual(errors, {})
  assert.equal(values.title, '')
  assert.equal(values.source, draft().source)
  assert.equal(values.description, '一封来信')
  assert.equal(values.episodeCount, 1)
})

test('personal and team creation preserve the selected episode count through 120 episodes', () => {
  for (const constraints of [{ kind: 'drama' }, { kind: 'drama', titleRequired: true }]) {
    for (const count of [1, 3, 6, 12, 25, 120]) {
      const { values, errors } = validateProjectCreate(draft({ title: '雨夜来信', episodeCount: String(count) }), constraints)
      assert.deepEqual(errors, {})
      assert.equal(values.episodeCount, count)
    }
  }
})

test('episode count rejects empty, fractional, and out of range values', () => {
  for (const count of ['', ' ', '0', '-1', '121', '1.5', 'abc', 'Infinity']) {
    assert.ok(validateProjectCreate(draft({ episodeCount: count }), { kind: 'drama' }).errors.episodeCount, count)
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
