import assert from 'node:assert/strict';
import test from 'node:test';
import { groupUpstreamRecords, redactTaskData, taskBilling, taskDurations, taskUpstream, upstreamRecords, upstreamStateLabel } from '../src/lib/taskObservability.ts';

test('existing local simulation events expose generation parameters without claiming a real HTTP request', () => {
  const event = { id: 3717, event_type: 'upstream.request.simulated', payload: {
    request_id: 'mock-request', operation: 'video.i2v', provider: 'local-mock', model: 'MiniMax-H3', simulated: true,
    request: { body: { prompt: '镜头缓慢推进', image_url: 'https://example.test/ref.jpg?token=secret', duration: 15, ratio: '9:16', generate_audio: false } },
  } };
  const summary = taskUpstream({ upstream_events: [event], provider_task_id: 'local-media-id' });
  assert.equal(summary.records.length, 1);
  assert.equal(summary.phase, '本地模拟 · 参数已记录');
  assert.equal(summary.provider, 'local-mock');
  const record = groupUpstreamRecords(summary.records)[0];
  assert.equal(record.operation, 'video.i2v');
  assert.equal(record.request.body.prompt, '镜头缓慢推进');
  assert.equal(record.request.body.duration, 15);
  assert.equal(record.request.body.generate_audio, false);
  assert.equal(record.httpStatus, undefined);
  assert.equal(record.method, undefined);
  assert.equal(record.response, undefined);
  assert.ok(!JSON.stringify(record).includes('secret'));
});

test('request and response credentials and signed URL values never reach presentation data', () => {
  const source = {
    headers: { Authorization: 'Bearer top-secret', 'x-api-key': 'key-value', Cookie: 'session-secret' },
    nested: { access_token: 'token-secret', client_secret: 'client-secret', 'x-client-token': 'header-secret' },
    result: 'https://user:pass@example.test/file.png?X-Amz-Signature=signed-secret&expires=other-secret#fragment-secret',
    message: 'HTTP error Bearer message-secret api_key=plain-secret',
    body: '{"api_key":"json-secret"}',
    total_tokens: 42,
  };
  const result = JSON.stringify(redactTaskData(source));
  for (const secret of ['top-secret', 'key-value', 'session-secret', 'token-secret', 'client-secret', 'header-secret', 'signed-secret', 'other-secret', 'fragment-secret', 'message-secret', 'plain-secret', 'json-secret', 'user:pass']) {
    assert.ok(!result.includes(secret), `leaked ${secret}`);
  }
  assert.ok(result.includes('file.png'));
  assert.ok(result.includes('42'));
  assert.equal(source.headers.Authorization, 'Bearer top-secret');
});

test('queued, active and terminal tasks retain distinct waiting and running durations', () => {
  const created_at = '2026-10-20T00:00:00Z';
  const now = Date.parse('2026-10-20T00:01:00Z');
  assert.deepEqual(taskDurations({ status: 'pending', created_at }, now), { waitingMs: 60_000, runningMs: null, totalMs: 60_000, live: true });
  assert.deepEqual(taskDurations({ status: 'awaiting_poll', created_at, started_at: '2026-10-20T00:00:10Z' }, now), { waitingMs: 10_000, runningMs: 50_000, totalMs: 60_000, live: true });
  assert.deepEqual(taskDurations({ status: 'succeeded', created_at, started_at: '2026-10-20T00:00:10Z', finished_at: '2026-10-20T00:00:40Z' }, now), { waitingMs: 10_000, runningMs: 30_000, totalMs: 40_000, live: false });
  assert.equal(taskDurations({ status: 'failed', created_at }, now).totalMs, null);
});

test('frozen zero charge is unsettled, while settled zero is a real final amount', () => {
  const frozen = taskBilling({ billing_status: 'frozen', billing_estimate_fen: 200, billing_charged_fen: 0 });
  assert.equal(frozen.settled, false);
  assert.equal(frozen.frozenFen, 200);
  const settled = taskBilling({ billing_status: 'settled', billing_estimate_fen: 200, billing_charged_fen: 0, billing_refunded_fen: 200 });
  assert.equal(settled.settled, true);
  assert.equal(settled.frozenFen, 0);
  assert.equal(settled.refundedFen, 200);
});

test('upstream evidence comes only from recorded HTTP events, never task or step payloads', () => {
  const task = { payload: { model_id: 'requested-model', prompt: 'internal-task-payload' }, steps: [{ provider_name: 'legacy-provider', input_payload: { request: 'not-an-http-record' } }] };
  assert.deepEqual(taskUpstream(task).records, []);
  assert.equal(taskUpstream(task).modelFromRequest, false);
  assert.equal(taskUpstream(task).model, 'requested-model');
  const events = [
    { id: 3, event_type: 'upstream.request.accepted', payload: { request_id: 'request-1', model: 'resolved-model', provider_task_id: 'provider-1', elapsed_ms: 125, response: { status_code: 202, body: { status: 'pending' } } } },
    { id: 1, event_type: 'task.started', payload: { request: 'not-an-http-record' } },
    { id: 2, event_type: 'upstream.request.started', payload: { request_id: 'request-1', method: 'POST', endpoint: 'https://example.test/tasks?api_key=secret', request: { model: 'resolved-model' } } },
  ];
  const summary = taskUpstream({ ...task, events });
  assert.equal(summary.records.length, 2);
  assert.equal(summary.model, 'resolved-model');
  assert.equal(summary.modelFromRequest, true);
  assert.equal(summary.providerTaskId, 'provider-1');
  assert.equal(summary.latest.elapsedMs, 125);
  assert.equal(summary.latest.httpStatus, 202);
  assert.equal(summary.phase, '上游排队');
  const groups = groupUpstreamRecords(summary.records);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].requestId, 'request-1');
  assert.equal(groups[0].method, 'POST');
  assert.equal(groups[0].request.model, 'resolved-model');
  assert.equal(groups[0].response.body.status, 'pending');
  assert.ok(!JSON.stringify(summary).includes('api_key=secret'));
  assert.equal(upstreamRecords([events[0]]).length, 1, 'missing started event does not hide accepted evidence');
});

test('provider response status determines visible upstream phase without inventing task progress', () => {
  const events = [{ id: 1, event_type: 'upstream.request.poll', payload: { response: { body: { data: { status: 'running' } } } } }];
  assert.equal(taskUpstream({ events }).phase, '上游生成中');
  assert.equal(upstreamStateLabel('completed'), '上游完成');
  assert.equal(upstreamStateLabel('failed'), '上游失败');
  assert.equal(taskUpstream({}).phase, '尚无上游请求记录');
});

test('bounded dedicated upstream evidence works without inspecting omitted general history', () => {
  const task = {
    upstream_events: [
      { id: 64071, event_type: 'upstream.request.failed', payload: { model: 'resolved-model', status_code: 422, error: 'invalid body' } },
      { id: 64070, event_type: 'upstream.request.started', payload: { model: 'resolved-model', method: 'POST' } },
    ],
  };
  Object.defineProperty(task, 'events', { get() { throw new Error('general history must not be loaded'); } });
  const result = taskUpstream(task);
  assert.equal(result.records.length, 2);
  assert.equal(result.latest.id, 64071);
  assert.equal(result.latest.httpStatus, 422);
  assert.equal(result.phase, '请求失败');
  assert.equal(result.model, 'resolved-model');
  assert.deepEqual(taskUpstream({ upstream_events: [], events: [{ id: 1, event_type: 'upstream.request.started' }] }).records, []);
});
