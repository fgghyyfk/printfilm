import assert from 'node:assert/strict'
import { test } from 'node:test'
import { rolldown } from 'rolldown'
import { safeNextPath } from '../src/lib/authRedirect.ts'

// 载入真实客户端，只提供浏览器环境和受控fetch，不复制认证失效逻辑。
async function browserClient(t) {
  const previousWindow = globalThis.window
  const previousStorage = globalThis.localStorage
  let token = 'first-token'
  const redirects = []
  const location = {
    protocol: 'https:', hostname: 'studio.test', pathname: '/history', search: '?page=2', hash: '#video',
    replace: value => redirects.push(value),
  }
  globalThis.window = { location }
  globalThis.localStorage = {
    getItem: () => token,
    setItem: (_key, value) => { token = value },
    removeItem: () => { token = null },
  }
  t.after(() => {
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
    if (previousStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = previousStorage
  })
  const bundle = await rolldown({
    input: 'src/api/client.ts',
    plugins: [{
      name: 'controlled-api-base',
      transform(code, id) {
        if (id.endsWith('/src/api/client.ts')) return code.replace('import.meta.env.VITE_API_BASE', '""')
      },
    }],
  })
  const { output } = await bundle.generate({ format: 'esm' })
  await bundle.close()
  const client = await import(`data:text/javascript;base64,${Buffer.from(output[0].code).toString('base64')}#${Math.random()}`)
  return { client, redirects, location }
}

test('安全回跳保留站内query/hash，拒绝外站、反斜杠及控制字符', () => {
  const path = '/tools?source=https://media.test/a#detail'
  assert.equal(safeNextPath(path), path)
  for (const unsafe of ['https://evil.test', '//evil.test', '/\\evil.test', '/a\nb', '/a\u0000b', '/a\u007fb']) {
    assert.equal(safeNextPath(unsafe), '/')
  }
})

test('统一401早于自定义错误处理，登录失败及网络/403/5xx保留会话', async t => {
  const { client, redirects, location } = await browserClient(t)
  let status = 401
  let failNetwork = false
  t.mock.method(globalThis, 'fetch', async () => {
    if (failNetwork) throw new TypeError('controlled offline')
    return Response.json({ detail: '认证失败' }, { status, headers: { 'X-Error-Code': 'auth_expired' } })
  })
  for (const path of ['/api/auth/login', '/api/auth/register', '/api/auth/forgot-password', '/api/auth/reset-password']) {
    await assert.rejects(client.createJsonRequester()(path))
    assert.equal(localStorage.getItem('token'), 'first-token')
  }
  for (status of [403, 500]) {
    await assert.rejects(client.requestResponse('/api/auth/me'))
    assert.equal(localStorage.getItem('token'), 'first-token')
  }
  failNetwork = true
  await assert.rejects(client.requestResponse('/api/auth/me'))
  assert.equal(localStorage.getItem('token'), 'first-token')
  assert.equal(redirects.length, 0)
  failNetwork = false
  status = 401
  await assert.rejects(client.requestResponse('/api/upload', { method: 'POST', body: new FormData() }, () => {
    assert.equal(localStorage.getItem('token'), null)
    assert.equal(redirects.length, 1)
    throw new Error('custom handler')
  }), /custom handler/)
  assert.equal(new URLSearchParams(redirects[0].split('?')[1]).get('next'), '/history?page=2#video')
  localStorage.setItem('token', 'new-token')
  location.pathname = '/auth'
  await assert.rejects(client.requestResponse('/api/auth/me'))
  assert.equal(localStorage.getItem('token'), null)
  assert.equal(redirects.length, 1)
})

test('迟到旧token及自定义凭据401不清新会话，并发只跳一次且新登录可再次失效', async t => {
  const { client, redirects } = await browserClient(t)
  const pending = []
  t.mock.method(globalThis, 'fetch', (_url, init) => new Promise(resolve => {
    pending.push({ resolve, authorization: init.headers.get('Authorization') })
  }))
  const old = client.requestResponse('/api/projects')
  localStorage.setItem('token', 'second-token')
  pending.shift().resolve(Response.json({ detail: '过期' }, { status: 401 }))
  await assert.rejects(old)
  assert.equal(localStorage.getItem('token'), 'second-token')
  const custom = client.requestResponse('/api/projects', { headers: { Authorization: 'Bearer custom-token' } })
  assert.equal(pending[0].authorization, 'Bearer custom-token')
  pending.shift().resolve(Response.json({ detail: '过期' }, { status: 401 }))
  await assert.rejects(custom)
  assert.equal(localStorage.getItem('token'), 'second-token')
  assert.equal(redirects.length, 0)
  const both = [client.requestResponse('/api/projects'), client.createJsonRequester()('/api/auth/me')]
  pending.splice(0).forEach(({ resolve }) => resolve(Response.json({ detail: '过期' }, { status: 401 })))
  await Promise.allSettled(both)
  assert.equal(localStorage.getItem('token'), null)
  assert.equal(redirects.length, 1)
  localStorage.setItem('token', 'third-token')
  const nextSession = client.requestResponse('/api/projects')
  pending.shift().resolve(Response.json({ detail: '过期' }, { status: 401, headers: { 'X-Error-Code': 'auth_expired' } }))
  await assert.rejects(nextSession, error => error.status === 401 && error.code === 'auth_expired')
  assert.equal(localStorage.getItem('token'), null)
  assert.equal(redirects.length, 2)
})
