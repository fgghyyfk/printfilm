import { createBrowserRouter, RouterProvider, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import DialogHost from './components/ui/DialogHost'
import BillingAlertHost from './components/billing/BillingAlertHost'
import MyTasks from './components/MyTasks'
import { DramaGenQueuePanel } from './components/drama/DramaGenQueuePanel'
import AuthPage from './pages/AuthPage'
import HistoryPage from './pages/HistoryPage'
import HomePage from './pages/HomePage'
import MethodPage from './pages/MethodPage'
import ToolsPage from './pages/ToolsPage'
import ToolDetailPage from './pages/ToolDetailPage'
import DaihuoPage from './pages/DaihuoPage'
import RefinePage from './pages/ecom/RefinePage'
import StylePage from './pages/ecom/StylePage'
import HelpPage from './pages/HelpPage'
import { PrivacyPage, TermsPage } from './pages/LegalDocPage'
import ContactPage from './pages/ContactPage'
import SettingsPage from './pages/SettingsPage'
import PricingPage from './pages/PricingPage'
import StudioPage from './pages/StudioPage'
import TemplatesPage from './pages/TemplatesPage'
import CreateProjectPage from './pages/studio/CreateProjectPage'
import StyleConfigPage from './pages/studio/StyleConfigPage'
import StoryboardPage from './pages/studio/StoryboardPage'
import EditorPage from './pages/studio/EditorPage'
import DramaListPage from './pages/drama/DramaListPage'
import AssetLibraryPage from './pages/drama/AssetLibraryPage'
import ProjectWorkspacePage from './pages/drama/ProjectWorkspacePage'
import EpisodeEditPage from './pages/drama/EpisodeEditPage'
import EpisodesPage from './pages/drama/EpisodesPage'
import CanvasPage from './pages/drama/canvas/CanvasPage'
import EpisodeStoryboardPage from './pages/drama/episodeCanvas/EpisodeStoryboardPage'
import './styles/printfilm.css'
import './App.css'

/** 旧套图地址重定向到商品带货，保留 ?id= 等查询参数 */
function ToDaihuo() {
  const { search } = useLocation()
  return <Navigate to={`/daihuo${search}`} replace />
}

function AppRoutes() {
  return (
    <>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/method" element={<MethodPage />} />
        <Route path="/auth" element={<AuthPage />} />
        <Route path="/templates" element={<TemplatesPage />} />
        <Route path="/tools" element={<ToolsPage />} />
        <Route path="/daihuo" element={<DaihuoPage />} />
        <Route path="/ecom/refine" element={<RefinePage />} />
        <Route path="/ecom/style" element={<StylePage />} />
        <Route path="/tools/ecom-set" element={<ToDaihuo />} />
        <Route path="/tools/ecomset" element={<ToDaihuo />} />
        <Route path="/tools/:toolId" element={<ToolDetailPage />} />
        <Route path="/assets" element={<AssetLibraryPage />} />
        <Route path="/help" element={<HelpPage />} />
        <Route path="/terms" element={<TermsPage />} />
        <Route path="/privacy" element={<PrivacyPage />} />
        <Route path="/contact" element={<ContactPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/pricing" element={<PricingPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="/studio/new" element={<CreateProjectPage />} />
        <Route path="/studio/:id/style" element={<StyleConfigPage />} />
        <Route path="/studio/:id/editor" element={<EditorPage />} />
        <Route path="/studio/:id" element={<StoryboardPage />} />
        <Route path="/studio" element={<StudioPage />} />
        {/* Agent home (mirrors novel agent entry) */}
        <Route path="/drama" element={<DramaListPage />} />
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
      <MyTasks />
      <DramaGenQueuePanel />
      <BillingAlertHost />
      <DialogHost />
    </>
  )
}

// 路由实例稳定，切换语言不会重建保存器或浏览器历史。
const router = createBrowserRouter([{ path: "*", element: <AppRoutes /> }])

export default function App() {
  return <RouterProvider router={router} />
}
