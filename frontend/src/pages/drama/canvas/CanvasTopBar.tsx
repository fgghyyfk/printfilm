import { useI18n } from '../../../i18n'
import { translate as translateUi } from '../../../i18n/runtime'
import { workspaceScope } from '../../../features/teams/workspaceScope'
/** 画布顶栏：返回、标题、保存状态、设置占位 */
import { MyTasksButton } from '../../../components/MyTasks'
import { useState } from 'react'
import { ChevronLeft, Maximize2, Settings } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useCanvasStore } from './CanvasStore'
import type { CanvasSaveStatus } from './useCanvasAutoSave'

type Props = {
  variant?: 'fullscreen' | 'embedded'
}

/** 保存状态文案 */
function saveStatusLabel(status: CanvasSaveStatus): string | null {
  if (status === 'dirty') return translateUi("workflow.unsaved")
  if (status === 'saving') return translateUi("workflow.saving")
  if (status === 'saved') return translateUi("ecomSet.plan.saved")
  if (status === 'error') return translateUi("workflow.couldNotSave")
  return null
}

/** 渲染画布页顶部工具栏 */
export function CanvasTopBar({ variant = 'fullscreen' }: Props) {
  const { t: translateUi } = useI18n()
  const navigate = useNavigate()
  const { saveStatus, projectId, freeCanvasMode } = useCanvasStore()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const embedded = variant === 'embedded'
  const statusText = saveStatusLabel(saveStatus)

  return (
    <>
      <div className="fc-overlay fc-topbar">
        <div className="fc-topbar-left">
          {embedded ? null : (
            <button
              type="button"
              className="fc-icon-btn"
              aria-label={translateUi("workflow.back_11d024")}
              title={translateUi("workflow.back_11d024")}
              onClick={() => {
                if (freeCanvasMode) {
                  navigate(workspaceScope.teamId ? `/teams/${workspaceScope.teamId}?tab=projects` : '/canvas')
                  return
                }
                if (window.history.length > 1) navigate(-1)
                else navigate(`/drama/projects/${projectId}`)
              }}
            >
              <ChevronLeft size={20} strokeWidth={1.8} />
            </button>
          )}
          <span className="fc-topbar-title">
            {embedded ? translateUi("workflow.assetCanvas") : freeCanvasMode ? translateUi("workflow.freeCanvas") : translateUi("workflow.assetLayout")}
          </span>
          {statusText ? (
            <span
              className={`fc-save-pill${
                saveStatus === 'error'
                  ? ' is-error'
                  : saveStatus === 'dirty'
                    ? ' is-dirty'
                    : saveStatus === 'saving'
                      ? ' is-saving'
                      : ''
              }`}
            >
              <span className="fc-save-dot" />
              {statusText}
            </span>
          ) : null}
        </div>

        <div className="fc-topbar-right">
          <MyTasksButton />
          {embedded ? (
            <button
              type="button"
              className="fc-icon-btn"
              aria-label={translateUi("workflow.fullscreenCanvas")}
              title={translateUi("workflow.fullscreenCanvas")}
              onClick={() => navigate(`/drama/projects/${projectId}/canvas`)}
            >
              <Maximize2 size={18} strokeWidth={1.8} />
            </button>
          ) : null}
          <button
            type="button"
            className="fc-icon-btn"
            aria-label={translateUi("workflow.settings")}
            title={translateUi("workflow.settings")}
            aria-expanded={settingsOpen}
            onClick={() => setSettingsOpen((v) => !v)}
          >
            <Settings size={18} strokeWidth={1.8} />
          </button>
        </div>
      </div>

      {settingsOpen ? (
        <div className="fc-settings-pop" role="dialog" aria-label={translateUi("workflow.canvasSettings")}>
          <strong>{translateUi("workflow.canvasSettings")}</strong>
          {freeCanvasMode
            ? translateUi("workflow.addNodesConnectThemAndGenerateImagesOrVideo")
            : translateUi("workflow.theLayoutAndProjectAssetsSyncAutomaticallyUploadsUse")}
          <div style={{ marginTop: 10 }}>
            <button
              type="button"
              className="fc-icon-btn is-sm"
              style={{ width: 'auto', padding: '0 12px', borderRadius: 10 }}
              onClick={() => setSettingsOpen(false)}
            >
              {translateUi("workflow.close")}
            </button>
          </div>
        </div>
      ) : null}
    </>
  )
}
