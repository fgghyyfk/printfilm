import type { createFragmentAutosave } from './dramaFragmentAutosave'

type Editor = ReturnType<typeof createFragmentAutosave>
type BlockedNavigation = { proceed?: () => void; reset?: () => void }

/** 保存器会串行排空 A 在途后新改的 B；失败时保留页面及草稿。 */
export async function flushFragmentNavigation(editor: Editor, navigation: BlockedNavigation, active: () => boolean) {
  try {
    await editor.flush()
    if (active()) navigation.proceed?.()
  } catch {
    if (active()) navigation.reset?.()
  }
}
