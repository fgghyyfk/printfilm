import assert from 'node:assert/strict'
import { test } from 'node:test'
import { rolldown } from 'rolldown'

// 直接执行真实错误展示函数，内存bundle避免修改项目import契约或新增测试框架。
const bundle = await rolldown({ input: 'src/lib/dramaGenError.ts' })
const output = await bundle.generate({ format: 'esm' })
await bundle.close()
const { formatDramaGenError } = await import(`data:text/javascript;base64,${Buffer.from(output.output[0].code).toString('base64')}`)
const secret = 'sk-synthetic-secret https://upstream.invalid?key=synthetic /private/tmp/synthetic.trace'

test('公开生成错误不透传旧队列中中文或网络错误里的敏感诊断', () => {
  for (const raw of [`服务异常 ${secret}`, `网络错误 ConnectError ${secret}`, `ReadTimeout ${secret}`]) {
    const view = formatDramaGenError(raw)
    assert.doesNotMatch(JSON.stringify(view), /sk-synthetic|upstream\.invalid|\/private\/tmp/)
  }
})

test('上游账户故障不触发用户钱包充值，已知内容错误保留可操作类别', () => {
  const upstream = formatDramaGenError(`Credits insufficient ${secret}`)
  assert.equal(upstream.upstreamAccountBlocked, true)
  assert.notEqual(upstream.billingBlocked, true)
  assert.doesNotMatch(JSON.stringify(upstream), /sk-synthetic|upstream\.invalid/)
  assert.match(formatDramaGenError('参考图疑似真人').message, /参考图|输入图片/)
  assert.match(formatDramaGenError('参考音频过短').message, /参考音频|音色/)
})
