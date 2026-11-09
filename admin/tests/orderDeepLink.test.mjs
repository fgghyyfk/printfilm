import assert from 'node:assert/strict';
import test from 'node:test';
import { withoutOrderDeepLink } from '../src/lib/orderDeepLink.ts';

test('关闭订单详情同时清掉 order 和 trade，否则 trade 深链会再次打开弹窗', () => {
  const next = withoutOrderDeepLink(new URLSearchParams('tab=orders&trade=PF1700000000000112ab34cd&order=9'));
  assert.equal(next.has('trade'), false);
  assert.equal(next.has('order'), false);
  assert.equal(next.get('tab'), 'orders');
});

test('清理深链不修改传入的参数', () => {
  const params = new URLSearchParams('trade=PF1');
  withoutOrderDeepLink(params);
  assert.equal(params.get('trade'), 'PF1');
});
