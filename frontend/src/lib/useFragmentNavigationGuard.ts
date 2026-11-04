import { useEffect } from 'react'
import { useBlocker } from 'react-router-dom'
import type { createFragmentAutosave } from './dramaFragmentAutosave'
import { flushFragmentNavigation } from './fragmentNavigation'

export function useFragmentNavigationGuard(editor: ReturnType<typeof createFragmentAutosave>) {
  const blocker = useBlocker(() => {
    const state = editor.state()
    return state.dirty || state.saving
  })
  useEffect(() => {
    if (blocker.state !== 'blocked') return
    let active = true
    void flushFragmentNavigation(editor, blocker, () => active && editor.isActive())
    return () => { active = false }
  }, [blocker, editor])
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      const state = editor.state()
      if (state.dirty || state.saving) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [editor])
}
