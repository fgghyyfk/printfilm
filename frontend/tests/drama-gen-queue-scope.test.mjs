import test from 'node:test'
import assert from 'node:assert/strict'
import { getDramaGenQueue, upsertDramaGenJob } from '../src/lib/dramaGenQueue.ts'
import { setWorkspaceTeam } from '../src/features/teams/workspaceScope.ts'

function addJob(id, title) {
  upsertDramaGenJob({ id, kind: 'image', projectId: 1, targetId: 1, title, subtype: '角色', status: 'running' })
}

test('团队队列可跟踪任务，切换空间不带入个人或其他团队任务', () => {
  setWorkspaceTeam(null, true)
  addJob('personal', '个人任务')
  assert.equal(getDramaGenQueue()[0].title, '个人任务')
  setWorkspaceTeam(2)
  assert.deepEqual(getDramaGenQueue(), [])
  addJob('team-2', '团队任务')
  assert.equal(getDramaGenQueue()[0].title, '团队任务')
  setWorkspaceTeam(3)
  assert.deepEqual(getDramaGenQueue(), [])
  setWorkspaceTeam(null)
  assert.deepEqual(getDramaGenQueue(), [])
})

test('同一空间换账号时清除旧身份队列', () => {
  setWorkspaceTeam(2)
  addJob('old-account', '旧账号任务')
  setWorkspaceTeam(2, true)
  assert.deepEqual(getDramaGenQueue(), [])
  setWorkspaceTeam(null)
})
