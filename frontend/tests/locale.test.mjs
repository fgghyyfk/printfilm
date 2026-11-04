import assert from 'node:assert/strict'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { rolldown } from 'rolldown'

const modulePath = (name) => JSON.stringify(fileURLToPath(new URL(`../src/${name}`, import.meta.url)))
const entry = 'virtual:locale-contract'
const bundle = await rolldown({
  input: entry,
  plugins: [{
    name: 'actual-locale-contract',
    resolveId(id) { if (id === entry) return entry },
    load(id) {
      if (id !== entry) return null
      return [
        `export { messages } from ${modulePath('i18n/messages.ts')}`,
        `export { lookupMessage, interpolate } from ${modulePath('i18n/lookup.ts')}`,
        `export { translate } from ${modulePath('i18n/runtime.ts')}`,
        `export { applyLocale } from ${modulePath('i18n/detect.ts')}`,
        `export { handleBillingError } from ${modulePath('lib/billingError.ts')}`,
        `export { getDialogRequest, closeDialog } from ${modulePath('lib/dialog.ts')}`,
        `export { HOME_CATEGORY_LABELS } from ${modulePath('lib/categories.ts')}`,
      ].join('\n')
    },
  }],
})
const { output } = await bundle.generate({ format: 'esm' })
await bundle.close()
const { messages, lookupMessage, interpolate, translate, applyLocale, handleBillingError, getDialogRequest, closeDialog, HOME_CATEGORY_LABELS } = await import(`data:text/javascript;base64,${Buffer.from(output[0].code).toString('base64')}`)

function leaves(value, prefix = '', result = {}) {
  if (typeof value === 'string') result[prefix] = value
  else if (value && typeof value === 'object') {
    for (const [key, child] of Object.entries(value)) leaves(child, prefix ? `${prefix}.${key}` : key, result)
  }
  return result
}
const placeholders = (text) => [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort()

test('actual Chinese and English catalogs have identical leaf paths and interpolation variables', () => {
  const zh = leaves(messages.zh)
  const en = leaves(messages.en)
  assert.deepEqual(Object.keys(en).sort(), Object.keys(zh).sort())
  for (const key of Object.keys(zh)) assert.deepEqual(placeholders(en[key]), placeholders(zh[key]), key)
})

test('lookup and interpolation execute against the real catalogs, including array paths', () => {
  assert.equal(lookupMessage(messages.en, 'home.flow.0'), undefined)
  assert.equal(lookupMessage(messages.en, 'not.a.message'), undefined)
  const zh = leaves(messages.zh)
  const entry = Object.entries(zh).find(([, value]) => value.includes('{v1}') && value.includes('{v2}'))
  assert.ok(entry, 'a real workflow message with multiple variables exists')
  const raw = lookupMessage(messages.en, entry[0])
  assert.equal(typeof raw, 'string')
  const rendered = interpolate(raw, { v1: '😀', v2: '24' })
  assert.ok(rendered.includes('😀'))
  assert.ok(rendered.includes('24'))
  assert.doesNotMatch(rendered, /\{v[12]\}/)
  assert.equal(interpolate('{value} / {missing}', { value: 'OK' }), 'OK / {missing}')
  const arrayLeaf = Object.entries(zh).find(([key]) => /\.\d+\./.test(key))
  assert.ok(arrayLeaf)
  assert.equal(lookupMessage(messages.zh, arrayLeaf[0]), arrayLeaf[1])
})

test('new billing dialogs use the locale at call time while retaining the original safe detail', async () => {
  const original = '余额不足（402），请先充值'
  try {
    for (const [locale, title] of [['en', /balance/i], ['zh', /余额/], ['en', /balance/i]]) {
      applyLocale(locale, false)
      const pending = handleBillingError(new Error(original))
      const request = getDialogRequest()
      assert.equal(request?.kind, 'confirm')
      assert.match(request.options.title, title)
      assert.equal(request.options.message, original)
      assert.ok(request.options.confirmText)
      assert.ok(request.options.cancelText)
      request.resolve(false)
      closeDialog()
      assert.equal(await pending, true)
    }
  } finally {
    closeDialog()
    applyLocale('zh', false)
  }
})

test('category display follows locale without changing the original lookup value', () => {
  try {
    applyLocale('zh', false)
    assert.equal(HOME_CATEGORY_LABELS['科普'], '知识科普')
    applyLocale('en', false)
    assert.equal(HOME_CATEGORY_LABELS['科普'], 'Knowledge explainers')
    assert.equal(translate('missing.message'), 'missing.message')
  } finally {
    applyLocale('zh', false)
  }
})
