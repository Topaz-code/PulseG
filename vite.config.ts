import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

/**
 * Two ways this app runs, and the config has to serve both.
 *
 * 1. Inside Tauri, the Python sidecar serves the built bundle from `dist` on 127.0.0.1:8787, so
 *    the app is same-origin and every relative URL (`/api/...`, `/ws/events`, `/media/...`,
 *    `/preview/...`) just works.
 * 2. During development, Vite serves the UI on 1420 and proxies those same paths to the Python
 *    process. Relative URLs stay identical in both modes - no `VITE_API_URL` branch anywhere in
 *    the source, which is where "works in dev, breaks in the installer" bugs come from.
 */
const BACKEND = process.env.PULSEG_BACKEND_URL ?? "http://127.0.0.1:8787";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  clearScreen: false,
  server: {
    port: 1420,
    // 0.0.0.0 so the sandbox preview and the Tauri webview can both reach the dev server.
    host: "0.0.0.0",
    strictPort: true,
    proxy: {
      "/api": { target: BACKEND, changeOrigin: true },
      "/media": { target: BACKEND, changeOrigin: true },
      "/preview": { target: BACKEND, changeOrigin: true },
      "/ws": { target: BACKEND, ws: true, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    target: "esnext",
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          graph: ["@xyflow/react"],
          charts: ["recharts"],
          markdown: ["react-markdown", "remark-gfm"],
        },
      },
    },
  },
});
