import assert from 'node:assert/strict'
import test from 'node:test'
import { mediaQuoteFen } from '../src/lib/mediaQuote.ts'

test('round each image before multiplying count', () => {
  assert.equal(mediaQuoteFen('0.0125', 3), 6)
  assert.equal(mediaQuoteFen('0.1000', 2), 20)
})

test('video uses submitted seconds with exact decimal arithmetic', () => {
  assert.equal(mediaQuoteFen('0.1234', 1, 8), 99)
  assert.equal(mediaQuoteFen('0.2900', 1, 10), 290)
})

test('invalid or missing price never becomes free', () => {
  for (const price of [undefined, '', 'NaN', '-1', '0', '0.12345']) assert.equal(mediaQuoteFen(price), null)
  assert.equal(mediaQuoteFen('1', 1, 0), null)
  assert.equal(mediaQuoteFen('1', 1, 4.5), null)
})
