import { Navigate, Route, Routes } from "react-router-dom";
import MainLayout from "./layouts/MainLayout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Chat from "./pages/Chat";
import Sessions from "./pages/Sessions";
import Goals from "./pages/Goals";
import Files from "./pages/Files";
import Artifacts from "./pages/Artifacts";
import SelfStatus from "./pages/SelfStatus";
import Settings from "./pages/Settings";
import { useAuthStore } from "./stores/authStore";

function RequireAuth({ children }: { children: JSX.Element }) {
  const token = useAuthStore((s) => s.token);
  if (!token) return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
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
        <Route path="files" element={<Files />} />
        <Route path="artifacts" element={<Artifacts />} />
        <Route path="self" element={<SelfStatus />} />
        <Route path="settings" element={<Settings />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
