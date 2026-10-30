import assert from 'node:assert/strict'
import test from 'node:test'
import { loadSessionUser } from '../src/lib/sessionUser.ts'

/** 记录会话和用户回写，模拟在请求期间切换账号或销毁导航栏。 */
function sessionHarness(loadUser) {
  let token = 'first-token'
  let current = true
  const users = []
  let cleared = 0
  return {
    options: {
      loadUser,
      getToken: () => token,
      isCurrent: () => current,
      onUser: (user) => users.push(user),
      onUnauthorized: () => { token = null; cleared += 1 },
    },
    replaceToken: (next) => { token = next },
    invalidate: () => { current = false },
    state: () => ({ token, users, cleared }),
  }
}

/** 手动完成旧请求，覆盖请求返回前身份已变化的实际竞态。 */
function deferred() {
  let resolve
  let reject
  const promise = new Promise((resolvePromise, rejectPromise) => { resolve = resolvePromise; reject = rejectPromise })
  return { promise, resolve, reject }
}

test('当前会话明确401才清除登录凭证', async () => {
  const session = sessionHarness(async () => { throw Object.assign(new Error('凭证过期'), { status: 401 }) })
  await loadSessionUser(session.options)
  assert.deepEqual(session.state(), { token: null, users: [], cleared: 1 })
})

test('取消、网络故障、5xx和非401错误保留登录凭证', async () => {
  const failures = [new DOMException('切换团队', 'AbortError'), new TypeError('Failed to fetch'), Object.assign(new Error('数据库超时'), { status: 503 }), Object.assign(new Error('Forbidden'), { status: 403 }), new Error('401 without HTTP status')]
  for (const failure of failures) {
    const session = sessionHarness(async () => { throw failure })
    await loadSessionUser(session.options)
    assert.deepEqual(session.state(), { token: 'first-token', users: [], cleared: 0 })
  }
})

test('旧token迟到401不能清除新账号登录', async () => {
  const response = deferred()
  const session = sessionHarness(() => response.promise)
  const pending = loadSessionUser(session.options)
  session.replaceToken('second-token')
  response.reject(Object.assign(new Error('旧凭证失效'), { status: 401 }))
  await pending
  assert.deepEqual(session.state(), { token: 'second-token', users: [], cleared: 0 })
})

test('旧token迟到成功不能覆盖新账号资料', async () => {
  const response = deferred()
  const session = sessionHarness(() => response.promise)
  const pending = loadSessionUser(session.options)
  session.replaceToken('second-token')
  response.resolve({ id: 1, nickname: '旧账号' })
  await pending
  assert.deepEqual(session.state(), { token: 'second-token', users: [], cleared: 0 })
})

test('卸载或账号事件失效后的旧请求不再回写成功或401', async () => {
  for (const unauthorized of [false, true]) {
    const response = deferred()
    const session = sessionHarness(() => response.promise)
    const pending = loadSessionUser(session.options)
    session.invalidate()
    if (unauthorized) response.reject(Object.assign(new Error('凭证失效'), { status: 401 }))
    else response.resolve({ id: 1 })
    await pending
    assert.deepEqual(session.state(), { token: 'first-token', users: [], cleared: 0 })
  }
})

test('当前会话成功更新资料；未登录时不发资料请求', async () => {
  let calls = 0
  const user = { id: 1, nickname: '当前账号' }
  const session = sessionHarness(async () => { calls += 1; return user })
  await loadSessionUser(session.options)
  assert.deepEqual(session.state(), { token: 'first-token', users: [user], cleared: 0 })
  session.replaceToken(null)
  await loadSessionUser(session.options)
  assert.equal(calls, 1)
})
