import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Spin } from "antd";
import MainLayout from "./layouts/MainLayout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Chat from "./pages/Chat";
import { useAuthStore } from "./stores/authStore";

// 除登录页/首屏 Dashboard/高频对话页外，其余页面按路由懒加载
// （kanban_react_spa_replacement_plan.md §5 P11：生产构建产物体积优化）。
// 每个页面模块单独打包成一个 chunk，只有访问到对应路由时才下载，
// 避免所有 18 个 Tab 的代码一次性塞进首屏的单个 1MB+ 大 bundle。
const Sessions = lazy(() => import("./pages/Sessions"));
const Goals = lazy(() => import("./pages/Goals"));
const Workflows = lazy(() => import("./pages/Workflows"));
const GrowthAdvisor = lazy(() => import("./pages/GrowthAdvisor"));
const CapabilityLearning = lazy(() => import("./pages/CapabilityLearning"));
const EvolutionProposals = lazy(() => import("./pages/EvolutionProposals"));
const CronJobs = lazy(() => import("./pages/CronJobs"));
const GlobalSchedule = lazy(() => import("./pages/GlobalSchedule"));
const ExternalInput = lazy(() => import("./pages/ExternalInput"));
const Watchlist = lazy(() => import("./pages/Watchlist"));
const HybridExec = lazy(() => import("./pages/HybridExec"));
const Config = lazy(() => import("./pages/Config"));
const Users = lazy(() => import("./pages/Users"));
const Files = lazy(() => import("./pages/Files"));
const Artifacts = lazy(() => import("./pages/Artifacts"));
const SelfStatus = lazy(() => import("./pages/SelfStatus"));
const Settings = lazy(() => import("./pages/Settings"));

function RequireAuth({ children }: { children: JSX.Element }) {
  const token = useAuthStore((s) => s.token);
  if (!token) return <Navigate to="/login" replace />;
  return children;
}

function PageFallback() {
  return (
    <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
      <Spin />
    </div>
  );
}

export default function App() {
  return (
    <Suspense fallback={<PageFallback />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <MainLayout />
            </RequireAuth>
          }
        >
          <Route index element={<Dashboard />} />
          <Route path="chat" element={<Chat />} />
          <Route path="sessions" element={<Sessions />} />
          <Route path="goals" element={<Goals />} />
          <Route path="workflows" element={<Workflows />} />
          <Route path="growth" element={<GrowthAdvisor />} />
          <Route path="capability" element={<CapabilityLearning />} />
          <Route path="evolution" element={<EvolutionProposals />} />
          <Route path="cron" element={<CronJobs />} />
          <Route path="schedule" element={<GlobalSchedule />} />
          <Route path="external-input" element={<ExternalInput />} />
          <Route path="watchlist" element={<Watchlist />} />
          <Route path="hybrid-exec" element={<HybridExec />} />
          <Route path="config" element={<Config />} />
          <Route path="users" element={<Users />} />
          <Route path="files" element={<Files />} />
          <Route path="artifacts" element={<Artifacts />} />
          <Route path="self" element={<SelfStatus />} />
          <Route path="settings" element={<Settings />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
