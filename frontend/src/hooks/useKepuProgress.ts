import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Project } from '../api'
import { monitorKepuProgress } from '../lib/kepuProgress'
import type { ProjectProgress } from '../lib/kepuProgress'
import { isProjectBusy } from '../lib/kepuTaskControls'

/** 以稳定项目/任务标识绑定进度监听，空闲、切项目或卸载时停止。 */
export function useKepuProgress(project: Project | null, onProject: (project: Project) => void): (ProjectProgress & { connectionError?: string }) | null {
  const projectId = project?.id
  const active = isProjectBusy(project)
  const taskKey = (project?.active_tasks || []).map((task) => task.id).sort((a, b) => a - b).join(',')
  const updateRef = useRef(onProject)
  const [connectionError, setConnectionError] = useState<string | null>(null)
  const [latest, setLatest] = useState<{ projectId: number; taskKey: string; event: ProjectProgress } | null>(null)

  useEffect(() => { updateRef.current = onProject }, [onProject])
  useEffect(() => {
    if (!active || projectId == null) return
    setConnectionError(null)
    return monitorKepuProgress({
      taskIds: taskKey ? taskKey.split(',').map(Number) : [],
      connect: (signal) => api.projectEvents(projectId, signal),
      load: (signal) => api.getProject(projectId, { signal }),
      onProject: (next) => updateRef.current(next),
      onProgress: (event) => setLatest({ projectId, taskKey, event }),
      onConnectionError: setConnectionError,
    })
  }, [active, projectId, taskKey])

  if (!active) return null
  const event = latest?.projectId === projectId && latest?.taskKey === taskKey ? latest.event : null
  return event ? { ...event, connectionError: connectionError || undefined }
    : connectionError ? { event: 'progress', connectionError } : null
}
