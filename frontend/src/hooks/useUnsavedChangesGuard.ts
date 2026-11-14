import { useEffect, useRef } from 'react'
import { useBlocker } from 'react-router-dom'
import { dialog } from '../lib/dialog'

/** 有未保存修改时拦截站内跳转（询问用户）和刷新、关闭标签页（浏览器原生提示）。 */
export function useUnsavedChangesGuard(dirty: boolean, message: string) {
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname,
  )
  // 同一次拦截只弹一个确认框
  const askingRef = useRef(false)

  useEffect(() => {
    if (blocker.state !== 'blocked' || askingRef.current) return
    askingRef.current = true
    void dialog
      .confirm({ title: '有未保存的修改', message, confirmText: '放弃修改并离开', cancelText: '留在此页', tone: 'danger' })
      .then((leave) => {
        if (leave) blocker.proceed()
        else blocker.reset()
      })
      .finally(() => {
        askingRef.current = false
      })
  }, [blocker, message])

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
}
