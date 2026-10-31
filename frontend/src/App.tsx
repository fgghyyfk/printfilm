import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { lazy, Suspense, useLayoutEffect, useSyncExternalStore } from 'react'
import DialogHost from './components/ui/DialogHost'
import BillingAlertHost from './components/billing/BillingAlertHost'
import { DramaGenQueuePanel } from './components/drama/DramaGenQueuePanel'
import AuthPage from './pages/AuthPage'
import HomePage from './pages/HomePage'
import RequireAuth from './pages/drama/RequireAuth'
import { setWorkspaceTeam, subscribeWorkspace, workspaceScope } from './features/teams/workspaceScope'
import { TeamPreviewProvider } from './features/teams/TeamPreviewContext'
import './styles/printfilm.css'
import './App.css'
import DocumentTitle from './components/layout/DocumentTitle'

/* 除首页与登录页外，页面按路由拆包，首屏只下载需要的代码 */
const HistoryPage = lazy(() => import('./pages/HistoryPage'))
const GenerationPage = lazy(() => import('./pages/GenerationPage'))
const GenerationTasksPage = lazy(() => import('./pages/GenerationTasksPage'))
const HelpPage = lazy(() => import('./pages/HelpPage'))
const PrivacyPage = lazy(() => import('./pages/LegalDocPage').then((mod) => ({ default: mod.PrivacyPage })))
const TermsPage = lazy(() => import('./pages/LegalDocPage').then((mod) => ({ default: mod.TermsPage })))
const ContactPage = lazy(() => import('./pages/ContactPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const PricingPage = lazy(() => import('./pages/PricingPage'))
const StudioPage = lazy(() => import('./pages/StudioPage'))
const TemplatesPage = lazy(() => import('./pages/TemplatesPage'))
const CreateProjectPage = lazy(() => import('./pages/studio/CreateProjectPage'))
const CreationSessionEditorBridge = lazy(() => import('./pages/studio/CreationSessionEditorBridge'))
const StyleConfigPage = lazy(() => import('./pages/studio/StyleConfigPage'))
const StoryboardPage = lazy(() => import('./pages/studio/StoryboardPage'))
const EditorPage = lazy(() => import('./pages/studio/EditorPage'))
const DramaListPage = lazy(() => import('./pages/drama/DramaListPage'))
const CanvasListPage = lazy(() => import('./pages/drama/CanvasListPage'))
const AssetLibraryPage = lazy(() => import('./pages/drama/AssetLibraryPage'))
const ProjectWorkspacePage = lazy(() => import('./pages/drama/ProjectWorkspacePage'))
const EpisodeEditPage = lazy(() => import('./pages/drama/EpisodeEditPage'))
const EpisodesPage = lazy(() => import('./pages/drama/EpisodesPage'))
const CanvasPage = lazy(() => import('./pages/drama/canvas/CanvasPage'))
const EpisodeStoryboardPage = lazy(() => import('./pages/drama/episodeCanvas/EpisodeStoryboardPage'))
const TeamInvitationPage = lazy(() => import('./features/teams/TeamInvitationPage'))
const TeamPage = lazy(() => import('./features/teams/TeamPage'))
const Marketplace = lazy(() => import('./features/marketplace/Marketplace'))

/** 页面代码加载中的占位 */
function PageFallback() {
  return <div className="pf-page-loading" role="status" aria-live="polite"><span className="pf-page-loading-dot" />加载中…</div>
}


/** 生成队列支持个人和团队空间，余额引导仅在个人业务空间展示。 */
function PersonalBusinessOverlays() {
  const { pathname } = useLocation()
  const teamId = useSyncExternalStore(subscribeWorkspace, () => workspaceScope.teamId)
  useLayoutEffect(() => { if (!/^\/teams\/\d+/.test(pathname) && !/^\/drama\/projects\/\d+/.test(pathname)) setWorkspaceTeam(null) }, [pathname])
  const personal = teamId === null && pathname !== '/teams' && !pathname.startsWith('/teams/') && pathname !== '/team-invitations'
  return <><DramaGenQueuePanel />{personal && <BillingAlertHost />}</>
}

/** 用户端路由及全局交互宿主，团队界面使用真实认证服务。 */
export default function App() {
  return (
    <TeamPreviewProvider><BrowserRouter>
      <Suspense fallback={<PageFallback />}>
      <Routes>
        <Route path="/marketplace/*" element={<Suspense fallback={<p>正在加载作品与招募…</p>}><Marketplace /></Suspense>} />
        <Route path="/" element={<HomePage />} />
        <Route path="/auth" element={<AuthPage />} />
        <Route path="/templates" element={<TemplatesPage />} />
        <Route path="/tools/video" element={<RequireAuth><GenerationPage kind="video" /></RequireAuth>} />
        <Route path="/tools/image" element={<RequireAuth><GenerationPage kind="image" /></RequireAuth>} />
        <Route path="/tools/audio" element={<RequireAuth><GenerationPage kind="audio" /></RequireAuth>} />
        <Route path="/tasks" element={<RequireAuth><GenerationTasksPage /></RequireAuth>} />
        <Route path="/assets" element={<AssetLibraryPage />} />
        <Route path="/help" element={<HelpPage />} />
        <Route path="/terms" element={<TermsPage />} />
        <Route path="/privacy" element={<PrivacyPage />} />
        <Route path="/contact" element={<ContactPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/teams" element={<RequireAuth><TeamPage /></RequireAuth>} />
        <Route path="/teams/join" element={<TeamInvitationPage />} />
        <Route path="/team-invitations" element={<TeamInvitationPage />} />
        <Route path="/teams/:teamId" element={<RequireAuth><TeamPage /></RequireAuth>} />
        <Route path="/pricing" element={<PricingPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/studio/new" element={<CreateProjectPage />} />
        <Route path="/studio/sessions/:sessionId" element={<CreationSessionEditorBridge />} />
        <Route path="/studio/:id/style" element={<StyleConfigPage />} />
        <Route path="/studio/:id/editor" element={<EditorPage />} />
        <Route path="/studio/:id" element={<StoryboardPage />} />
        <Route path="/studio" element={<StudioPage />} />
        {/* Agent home (mirrors novel agent entry) */}
        <Route path="/drama" element={<DramaListPage />} />
        <Route path="/canvas" element={<CanvasListPage />} />
        <Route path="/drama/dramas" element={<DramaListPage />} />
        <Route path="/drama/assets" element={<AssetLibraryPage />} />
        <Route path="/drama/projects/:projectId" element={<ProjectWorkspacePage />} />
        <Route path="/drama/projects/:projectId/episodes" element={<EpisodesPage />} />
        <Route path="/drama/projects/:projectId/episodes/:episodeId" element={<EpisodeEditPage />} />
        <Route
          path="/drama/projects/:projectId/episodes/:episodeId/canvas"
          element={<EpisodeStoryboardPage />}
        />
        <Route path="/drama/projects/:projectId/canvas" element={<CanvasPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      </Suspense>
      <PersonalBusinessOverlays />
      <DocumentTitle />
      <DialogHost />
    </BrowserRouter></TeamPreviewProvider>
  )
}
