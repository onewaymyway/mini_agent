import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider, message } from "antd";
import zhCN from "antd/locale/zh_CN";
import App from "./App";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
    },
    mutations: {
      // 兜底：任何写操作 mutation 只要没在调用点单独传 onError，失败时至少
      // 弹一条 toast，而不是像之前那样"按钮 loading 消失、界面毫无反应"，
      // 让用户误以为操作卡住了或者已经悄悄生效。调用点自己传了 onError 的
      // 话会覆盖这里（TanStack Query 的行为），不影响已经处理好的场景。
      onError: (err: unknown) => {
        message.error(err instanceof Error ? err.message : "操作失败，请重试");
      },
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: "#1677ff" } }}>
        <BrowserRouter basename={import.meta.env.PROD ? "/kanban" : "/"}>
          <App />
        </BrowserRouter>
      </ConfigProvider>
    </QueryClientProvider>
  </React.StrictMode>
);
