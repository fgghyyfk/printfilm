import assert from 'node:assert/strict';
import test from 'node:test';
import { taskEventPagePath } from '../src/lib/taskEventPagination.ts';

test('event history requests only the selected bounded page', () => {
  assert.equal(taskEventPagePath(6807, 1282, 50), '/api/admin/tasks/6807/events?page=1282&page_size=50');
  assert.equal(taskEventPagePath(6807, 0, 1000), '/api/admin/tasks/6807/events?page=1&page_size=100');
  assert.equal(taskEventPagePath(6807, NaN, Infinity), '/api/admin/tasks/6807/events?page=1&page_size=50');
});

test('upstream pagination is explicitly filtered instead of paging through unrelated history', () => {
  const url = new URL(taskEventPagePath(6807, 2, 20, 'upstream.request.'), 'https://admin.example.test');
  assert.equal(url.pathname, '/api/admin/tasks/6807/events');
  assert.equal(url.searchParams.get('page'), '2');
  assert.equal(url.searchParams.get('page_size'), '20');
  assert.equal(url.searchParams.get('event_type_prefix'), 'upstream.request.');
});
