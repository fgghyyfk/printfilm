import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createHttpTransport } from '../src/api/http.ts'
import { monitorKepuProgress, readProjectEvents } from '../src/lib/kepuProgress.ts'

// 使用真实 ReadableStream 控制分块与断流，不连接外部服务。
function eventStream() {
  let controller
  let cancelled = false
  const stream = new ReadableStream({
    start(value) { controller = value },
    cancel() { cancelled = true },
  })
  const encoder = new TextEncoder()
  return {
    stream,
    bytes(value) { controller.enqueue(value) },
    send(value) { controller.enqueue(encoder.encode(value)) },
    close() { controller.close() },
    get cancelled() { return cancelled },
  }
}

// 等待流解码、fetch 和回读 Promise 的微任务完成。
async function settle() {
  for (let n = 0; n < 20; n += 1) await Promise.resolve()
}

// 以服务端活动任务表识别忙碌，项目旧 status 不充当终态依据。
const activeProject = { id: 7, status: 'IMAGING', active_tasks: [{ id: 91, status: 'running' }] }

test('SSE 按完整帧解析跨块 UTF-8、CRLF、多行 data，忽略 ping 和注释', async () => {
  const source = eventStream()
  const received = []
  const reading = readProjectEvents(source.stream, (event) => received.push(event), new AbortController().signal)
  const bytes = new TextEncoder().encode(': heartbeat\r\nevent: ping\r\ndata: {}\r\n\r\nevent: message\r\ndata: {"event":"progress",\r\ndata: "message":"出图 3/8","shot":3}\r\n\r\n')
  // 包含中文字符中间与 CR/LF 之间的切分。
  for (let n = 0; n < bytes.length; n += 1) source.bytes(bytes.slice(n, n + 1))
  source.close()
  await reading
  assert.deepEqual(received, [{ event: 'progress', message: '出图 3/8', shot: 3 }])
})

test('SSE 跳过坏 JSON、未知消息和未结束的尾帧', async () => {
  const source = eventStream()
  const received = []
  const reading = readProjectEvents(source.stream, (event) => received.push(event), new AbortController().signal)
  source.send('data: broken\n\ndata: {"event":"unknown"}\n\ndata: {"event":"paused"}\n\ndata: {"event":"done"}')
  source.close()
  await reading
  assert.deepEqual(received, [{ event: 'paused' }])
})

test('SSE abort 取消 reader，之后不再回调', async () => {
  const source = eventStream()
  const abort = new AbortController()
  const received = []
  const reading = readProjectEvents(source.stream, (event) => received.push(event), abort.signal)
  abort.abort()
  await reading
  assert.equal(source.cancelled, true)
  assert.deepEqual(received, [])
})

test('鉴权 SSE 经过 transport，重复进度幂等且不写项目状态', async (t) => {
  const source = eventStream()
  let request
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    request = { url, init }
    return new Response(source.stream)
  })
  const transport = createHttpTransport({ baseUrl: 'http://api.test', getToken: () => 'synthetic', onError() { throw new Error('请求失败') } })
  const progress = []
  const projects = []
  const stop = monitorKepuProgress({
    taskIds: [91],
    connect: (signal) => transport.response('/api/projects/7/events', { signal }),
    load: async () => activeProject,
    onProgress: (event) => progress.push(event),
    onProject: (project) => projects.push(project),
  })
  await settle()
  source.send('data: {"event":"progress","task_id":91,"stage":"PARALLEL_ASSETS","percent":31,"shot":3,"message":"出图 3/8"}\n\n'.repeat(2))
  await settle()
  stop()
  assert.equal(request.url, 'http://api.test/api/projects/7/events')
  assert.equal(request.init.headers.get('Authorization'), 'Bearer synthetic')
  assert.equal(request.url.includes('synthetic'), false)
  assert.equal(request.init.signal.aborted, true)
  assert.equal(source.cancelled, true)
  assert.equal(progress.length, 1)
  assert.equal(progress[0].percent, 31)
  assert.deepEqual(projects, [])
})

test('旧终态只触发回读，仍有新任务时继续轮询且断流延迟重连', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const source = eventStream()
  let connections = 0
  let loads = 0
  let signal
  const progress = []
  const projects = []
  const stop = monitorKepuProgress({
    taskIds: [91],
    connect: async (value) => { signal = value; connections += 1; return new Response(source.stream) },
    load: async () => { loads += 1; return activeProject },
    onProgress: (event) => progress.push(event),
    onProject: (project) => projects.push(project),
  })
  await settle()
  source.send('data: {"event":"done","percent":100,"message":"旧成片完成"}\n\n')
  source.close()
  await settle()
  assert.equal(loads, 1)
  assert.equal(signal.aborted, false)
  assert.deepEqual(progress, [])
  assert.deepEqual(projects, [activeProject])
  assert.equal(connections, 1)
  t.mock.timers.tick(1500)
  await settle()
  assert.equal(loads, 2)
  assert.equal(connections, 1)
  stop()
})

test('canonical 回读为空闲时终止流与所有定时器', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const source = eventStream()
  let loads = 0
  let connections = 0
  const projects = []
  monitorKepuProgress({
    taskIds: [91],
    connect: async () => { connections += 1; return new Response(source.stream) },
    load: async () => { loads += 1; return { ...activeProject, status: 'VIDEO_READY', active_tasks: [] } },
    onProgress() {},
    onProject: (project) => projects.push(project),
  })
  await settle()
  source.send('data: {"event":"paused"}\n\n')
  await settle()
  assert.equal(source.cancelled, true)
  t.mock.timers.tick(30000)
  await settle()
  assert.equal(loads, 1)
  assert.equal(connections, 1)
  assert.equal(projects[0].status, 'VIDEO_READY')
})

test('轮询串行，切项目停止后旧回读和旧流不能回填', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  let resolveLoad
  let loads = 0
  let loadSignal
  const projects = []
  const source = eventStream()
  const stop = monitorKepuProgress({
    taskIds: [91],
    connect: async () => new Response(source.stream),
    load: (signal) => { loadSignal = signal; loads += 1; return new Promise((resolve) => { resolveLoad = resolve }) },
    onProgress() {},
    onProject: (project) => projects.push(project),
  })
  await settle()
  t.mock.timers.tick(5000)
  await settle()
  assert.equal(loads, 1)
  t.mock.timers.tick(30000)
  await settle()
  assert.equal(loads, 1)
  stop()
  resolveLoad(activeProject)
  await settle()
  assert.equal(loadSignal.aborted, true)
  assert.equal(source.cancelled, true)
  assert.deepEqual(projects, [])
})

test('重放的旧任务与无来源 progress 不高亮当前任务，terminal 仍回读', async () => {
  const source = eventStream()
  const progress = []
  let loads = 0
  const stop = monitorKepuProgress({
    taskIds: [91],
    connect: async () => new Response(source.stream),
    load: async () => { loads += 1; return activeProject },
    onProgress: (event) => progress.push(event),
    onProject() {},
  })
  await settle()
  source.send('data: {"event":"progress","task_id":90,"shot":8,"message":"旧任务出图"}\n\ndata: {"event":"progress","shot":6,"message":"旧无来源出图"}\n\ndata: {"event":"failed","task_id":90}\n\ndata: {"event":"progress","task_id":91,"shot":3,"message":"当前出图"}\n\n')
  await settle()
  stop()
  assert.equal(loads, 1)
  assert.deepEqual(progress, [{ event: 'progress', task_id: 91, shot: 3, message: '当前出图' }])
})

test('事件连接失败时按1.5秒回读，延迟重连后恢复健康流', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const source = eventStream()
  let connections = 0
  let loads = 0
  const progress = []
  const stop = monitorKepuProgress({
    taskIds: [91],
    connect: async () => {
      connections += 1
      if (connections === 1) throw new TypeError('离线')
      return new Response(source.stream)
    },
    load: async () => { loads += 1; return activeProject },
    onProgress: (event) => progress.push(event),
    onProject() {},
  })
  await settle()
  t.mock.timers.tick(1500)
  await settle()
  assert.equal(loads, 1)
  assert.equal(connections, 1)
  t.mock.timers.tick(1500)
  await settle()
  assert.equal(connections, 2)
  source.send('data: {"event":"progress","task_id":91,"shot":7,"message":"合成重试中"}\n\n')
  await settle()
  assert.equal(progress[0].shot, 7)
  stop()
  assert.equal(source.cancelled, true)
})
