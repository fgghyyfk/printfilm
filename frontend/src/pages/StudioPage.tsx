import { Navigate, useSearchParams } from 'react-router-dom'
import { legacyStudioEntryPath } from '../lib/shortVideoRoutes'

/** 旧短视频首页也进入项目卡片列表，明确的新建/项目链接保留。 */
export default function StudioPage() {
  const [params] = useSearchParams()
  return <Navigate to={legacyStudioEntryPath({ project: params.get('project'), template: params.get('template'), mode: params.get('mode') })} replace />
}
