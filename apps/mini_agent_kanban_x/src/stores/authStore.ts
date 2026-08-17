import { create } from "zustand";

const LS_TOKEN = "mak_token";
const LS_BASE = "mak_api_base";

interface AuthState {
  token: string;
  apiBase: string;
  setToken: (t: string) => void;
  setApiBase: (b: string) => void;
  clear: () => void;
}

// API Base 默认走同源 /v1（生产环境由 FastAPI StaticFiles 挂载 / dev 环境由 vite proxy 转发），
// 用户也可以在“设置”页里改成任意 daemon 地址（跨源直连）。
const defaultApiBase = () =>
  localStorage.getItem(LS_BASE) || import.meta.env.VITE_API_BASE || "/v1";

export const useAuthStore = create<AuthState>((set) => ({
  token: localStorage.getItem(LS_TOKEN) || "",
  apiBase: defaultApiBase(),
  setToken: (t) => {
    localStorage.setItem(LS_TOKEN, t);
    set({ token: t });
  },
  setApiBase: (b) => {
    localStorage.setItem(LS_BASE, b);
    set({ apiBase: b });
  },
  clear: () => {
    localStorage.removeItem(LS_TOKEN);
    set({ token: "" });
  },
}));
