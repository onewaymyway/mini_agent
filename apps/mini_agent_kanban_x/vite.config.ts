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
      rollupOptions: {
        output: {
          // 把体积最大的几个第三方依赖单独拆成 vendor chunk，
          // 与业务代码分开缓存——antd 版本升级频率远低于业务页面，
          // 拆开后浏览器可以长期复用这块缓存，不必每次发版都重新下载。
          manualChunks: {
            "vendor-react": ["react", "react-dom", "react-router-dom"],
            "vendor-antd": ["antd", "@ant-design/icons"],
            "vendor-query": ["@tanstack/react-query"],
          },
        },
      },
    },
  };
});
