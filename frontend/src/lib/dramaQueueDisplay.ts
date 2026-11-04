import { translate as translateUi } from '../i18n/runtime'

/** Translate the local queue vocabulary without changing persisted subtype/control values. */
export function queueDisplayText(value: string): string {
  const labels: Record<string, string> = {
    "排队中": translateUi("ecomSet.result.queued"),
    "生图中": translateUi("workflow.generatingImage"),
    "生视频中": translateUi("workflow.generatingVideo"),
    "生成中": translateUi("ecomSet.result.running"),
    "生成参考图…": translateUi("workflow.generatingReferences"),
    "已入队": translateUi("workflow.queued"),
    "已取消": translateUi("workflow.cancelled"),
    "任务已中断，请重新生成": translateUi("workflow.taskInterruptedGenerateAgain"),
    "分镜视频": translateUi("workflow.storyboardVideo"),
    "画布视频": translateUi("workflow.canvasVideo"),
  }
  return labels[value] || value
}
