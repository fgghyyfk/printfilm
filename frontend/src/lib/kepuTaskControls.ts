import { translate as translateUi } from '../i18n/runtime'
import type { Project } from '../api'
import type { ConfirmOptions } from './dialog'

/** 以统一任务列表识别当前活动任务。 */
export function hasActiveUnifiedTasks(project: Project | null): boolean {
  const activeStatuses = ['pending', 'leased', 'running', 'awaiting_poll', 'awaiting_review', 'cancel_requested']
  return Boolean(
    project?.active_tasks?.some(
      (task) => activeStatuses.includes(task.status),
    ),
  )
}

/** 有任务平台数据时以 active_tasks 为准，取消请求未终止前仍保持忙碌。 */
export function isProjectBusy(project: Project | null): boolean {
  if (!project) return false
  if (hasActiveUnifiedTasks(project)) return true
  if (Array.isArray(project.active_tasks)) {
    return false
  }
  return ['SCRIPTING', 'IMAGING', 'VIDEOING', 'AUDIOING', 'COMPOSING', 'AUDITING'].includes(project.status)
}

/** 确认后请求取消，取消对话框不改变项目状态；请求失败交由界面显示。 */
export async function requestProjectCancellation(
  projectId: number,
  confirm: (options: ConfirmOptions) => Promise<boolean>,
  cancel: (id: number) => Promise<Project>,
): Promise<Project | null> {
  const confirmed = await confirm({
    title: translateUi("workflow.cancelGeneration"),
    message: translateUi("workflow.cancelThisGenerationTaskWaitForRunningWorkTo"),
    confirmText: translateUi("workflow.cancelGeneration"),
    cancelText: translateUi("workflow.keepWaiting"),
    tone: 'danger',
  })
  if (!confirmed) return null
  return cancel(projectId)
}
