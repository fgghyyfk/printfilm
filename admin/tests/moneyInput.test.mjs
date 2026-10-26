import assert from 'node:assert/strict';
import test from 'node:test';
import { yuanInputToFen } from '../src/lib/moneyInput.ts';

test('money input converts decimal yuan to exact integer fen', () => {
  assert.equal(yuanInputToFen('0'), 0);
  assert.equal(yuanInputToFen('0.29'), 29);
  assert.equal(yuanInputToFen('1.01'), 101);
  assert.equal(yuanInputToFen(' 1000.9 '), 100090);
  assert.equal(yuanInputToFen('001.10'), 110);
  assert.equal(yuanInputToFen('90071992547409.91'), Number.MAX_SAFE_INTEGER);
});

test('money input rejects partial, negative, non-finite, rounded or unsafe values', () => {
  for (const value of ['', ' ', '-1', '+1', '1.', '.5', '1.001', '100abc', '1e3', 'Infinity', 'NaN', '90071992547409.92']) {
    assert.equal(yuanInputToFen(value), null, value);
  }
});
