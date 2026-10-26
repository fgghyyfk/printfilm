import { useEffect, useRef, useState } from 'react'
import { dialog } from '../../lib/dialog'
import { editLockError, holdEditLock, releaseEditLock, releaseLostEditLock, subscribeEditLocks, syncProjectWorkspace, type EditTarget } from './editLocks'
import { subscribeWorkspace } from './workspaceScope'

/** 页面级占用，等待/冲突/续租失效期间保持只读。 */
export function useTeamEditLock(target: EditTarget, enabled = true, refresh?: (isCurrent: () => boolean) => Promise<void>) {
  /* checking/error/retry 控制首次获取、只读反馈与显式重取。 */
  const [checking, setChecking] = useState(enabled)
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const forceNext = useRef<string | null>(null)
  const [readyKey, setReadyKey] = useState<string | null>(null)
  const refreshRef = useRef(refresh)
  refreshRef.current = refresh
  const { projectId, type, resourceId } = target
  const resourceKey = `${projectId}:${type}:${resourceId}`
  useEffect(() => {
    const current = { projectId, type, resourceId }
    const force = forceNext.current === `${projectId}:${type}:${resourceId}`
    forceNext.current = null
    if (!enabled || !projectId) { setChecking(false); setError(''); return }
    let active = true; let acquired: string | null = null
    setChecking(true); setError(''); setReadyKey(null)
    void syncProjectWorkspace(projectId).then(() => active ? holdEditLock(current, force) : null).then(async (held) => { acquired = held; if (!active) { if (held) void releaseEditLock(current, false, held); return }; await refreshRef.current?.(() => active); if (active) setReadyKey(`${projectId}:${type}:${resourceId}`) }).catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : '无法取得编辑权') }).finally(() => { if (active) setChecking(false) })
    const unsubscribe = subscribeEditLocks(() => { const message = editLockError(current); if (active && message) setError(message) })
    // syncProjectWorkspace 可能主动取消旧空间请求；该代结果作废并在新空间重新读取。
    const unsubscribeWorkspace = subscribeWorkspace(() => { if (active) { active = false; setAttempt((value) => value + 1) } })
    return () => { active = false; unsubscribe(); unsubscribeWorkspace(); if (acquired) void releaseEditLock(current, false, acquired) }
  }, [projectId, type, resourceId, enabled, attempt])
  /** 强制接管前说明对方未保存的编辑会被拒绝，后台任务仍由服务端保护。 */
  async function takeOver() { if (await dialog.confirm({ title: '接管编辑占用', message: '接管后，另一位成员的旧页面将无法继续保存。请先与对方沟通。系统生成任务不能强制接管。', confirmText: '确认接管', tone: 'danger' })) { forceNext.current = resourceKey; setAttempt((value) => value + 1) } }
  async function retry() { forceNext.current = null; setChecking(true); setError(''); await releaseLostEditLock({ projectId, type, resourceId }); setAttempt((value) => value + 1) }
  return { checking: enabled && (checking || (readyKey !== resourceKey && !error)), readOnly: enabled && (checking || readyKey !== resourceKey || Boolean(error)), error, retry, takeOver }
}
