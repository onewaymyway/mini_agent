import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发模式下把 /v1 代理到 mini-agent daemon，天然同源，避免 CORS 配置。
// daemon 地址可通过环境变量 VITE_DAEMON_TARGET 覆盖（默认 http://127.0.0.1:8765）。
export default defineConfig(({ mode }) => {
  const target = process.env.VITE_DAEMON_TARGET || "http://127.0.0.1:8765";
  return {
    plugins: [react()],
    base: mode === "production" ? "/kanban/" : "/",
    server: {
      port: 5173,
      proxy: {
        "/v1": {
          target,
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: "dist",
      sourcemap: false,
    },
  };
});
