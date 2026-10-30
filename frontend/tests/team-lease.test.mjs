import test from 'node:test'
import assert from 'node:assert/strict'
import { renewedLease, isSameLease } from '../src/features/teams/leaseIdentity.ts'

test('30秒续租响应不含token时，后续保存仍持有获取时的凭证', () => {
  const acquired = { token: 'secret-acquired', version: 8, expires_at: '2026-10-22T02:00:00Z', owner_user_id: 2, owner_name: '成员' }
  const renewed = renewedLease(acquired, { version: 8, expires_at: '2026-10-22T02:00:30Z', owner_user_id: 2, owner_name: '成员' })
  assert.equal(renewed.token, 'secret-acquired')
  assert.equal(renewed.expires_at, '2026-10-22T02:00:30Z')
  assert.equal(acquired.expires_at, '2026-10-22T02:00:00Z')
})

test('切走再切回后，旧请求finally和旧页面cleanup不能释放新租约', () => {
  const newLease = { token: 'new-generation' }
  assert.equal(isSameLease(newLease, 'old-generation'), false)
  assert.equal(isSameLease(newLease, 'new-generation'), true)
  assert.equal(isSameLease(undefined, 'old-generation'), false)
})
