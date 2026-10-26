import { useCallback, useEffect, useRef, useState } from 'react'
import { creationApi, type CreationSession } from '../api/creation'
import { isRequestAborted } from '../api/http'
import { isCreationBusy } from '../lib/creationSession'

/** 服务端持续制作；轮询只恢复界面，退出页面会取消请求但不会停止任务。 */
export function useCreationSession(
  sessionId: string | undefined,
  onUnauthorized: () => void,
) {
  const [session, setSession] = useState<CreationSession | null>(null)
  const [loading, setLoading] = useState(Boolean(sessionId))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [connectionError, setConnectionError] = useState(false)
  const current = useRef<CreationSession | null>(null)
  const route = useRef(sessionId)
  const revision = useRef(0)
  const mutation = useRef<AbortController | null>(null)
  const unauthorized = useRef(onUnauthorized)
  unauthorized.current = onUnauthorized
  route.current = sessionId

  const accept = useCallback((value: CreationSession) => {
    current.current = value
    setSession(value)
  }, [])

  const report = useCallback((cause: unknown) => {
    if (isRequestAborted(cause)) return
    if (
      cause &&
      typeof cause === 'object' &&
      'status' in cause &&
      cause.status === 401
    ) {
      unauthorized.current()
      return
    }
    setError(
      cause instanceof Error ? cause.message : '暂时无法连接，请稍后重试',
    )
  }, [])

  useEffect(() => {
    let disposed = false
    let retrying = true
    let timer: ReturnType<typeof setTimeout> | undefined
    let controller: AbortController | null = null
    mutation.current?.abort()
    mutation.current = null
    revision.current += 1
    setBusy(false)
    const matching = current.current && String(current.current.id) === sessionId
    if (!matching) {
      current.current = null
      setSession(null)
    }
    setError('')
    setConnectionError(false)
    setLoading(Boolean(sessionId && !matching))
    if (!sessionId) return

    async function refresh(initial = false) {
      if (disposed) return
      if (!initial && (!isCreationBusy(current.current) || mutation.current)) {
        timer = setTimeout(() => {
          void refresh()
        }, 2000)
        return
      }
      controller = new AbortController()
      const requestRevision = revision.current
      try {
        const value = await creationApi.get(sessionId!, controller.signal)
        if (!disposed && requestRevision === revision.current) {
          accept(value)
          setConnectionError(false)
          setError('')
        }
      } catch (cause) {
        if (disposed || isRequestAborted(cause)) return
        const status =
          cause && typeof cause === 'object' && 'status' in cause
            ? Number(cause.status)
            : 0
        if ([401, 403, 404].includes(status)) retrying = false
        if (status === 401) unauthorized.current()
        else if (!current.current) report(cause)
        else setConnectionError(true)
      } finally {
        if (!disposed) {
          setLoading(false)
          if (retrying)
            timer = setTimeout(() => {
              void refresh(!current.current)
            }, 2000)
        }
      }
    }
    void refresh(true)
    return () => {
      disposed = true
      controller?.abort()
      if (timer) clearTimeout(timer)
    }
  }, [sessionId, accept, report])

  useEffect(
    () => () => {
      mutation.current?.abort()
    },
    [],
  )

  const perform = useCallback(
    async (operation: (signal: AbortSignal) => Promise<CreationSession>) => {
      if (mutation.current) return null
      const controller = new AbortController()
      const startedAt = route.current
      mutation.current = controller
      revision.current += 1
      setBusy(true)
      setError('')
      try {
        const value = await operation(controller.signal)
        if (controller.signal.aborted || startedAt !== route.current)
          return null
        accept(value)
        setConnectionError(false)
        return value
      } catch (cause) {
        if (!controller.signal.aborted && startedAt === route.current)
          report(cause)
        return null
      } finally {
        if (mutation.current === controller) {
          mutation.current = null
          if (!controller.signal.aborted) setBusy(false)
        }
      }
    },
    [accept, report],
  )

  return {
    session,
    loading,
    busy,
    error,
    connectionError,
    perform,
    report,
    clearError: () => setError(''),
  }
}
