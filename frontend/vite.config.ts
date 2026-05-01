import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backendPort = process.env.ATELIER_BACKEND_PORT ?? "8001";
const backendHost = process.env.ATELIER_BACKEND_HOST ?? "127.0.0.1";

export default defineConfig({
  plugins: [react()],
  server: {
    host: process.env.ATELIER_FRONTEND_HOST ?? "127.0.0.1",
    // 5173 is Vite's stock default but it clashes with another app on the
    // user's machine, so Atelier uses 4173. Override via env var if needed.
    port: Number(process.env.ATELIER_FRONTEND_PORT ?? 4173),
    // Proxy /api/* to the backend, with WS upgrade support so the agent
    // stream WebSocket goes through the same origin (no CORS, no separate
    // wiring on the client).
    proxy: {
      "/api": {
        target: `http://${backendHost}:${backendPort}`,
        ws: true,
        changeOrigin: true,
      },
    },
  },
});
