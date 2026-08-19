/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev proxy mirrors the production nginx contract exactly: the browser
// talks to /api/* and the /api prefix is STRIPPED before the backend sees it.
//
// The target is overridable because "where the backend is" differs by where
// the dev server itself runs. On the host it is localhost:8000. Inside the dev
// container (docker-compose.dev.yaml) localhost is the FRONTEND container, so
// the compose file sets QATF_API_TARGET=http://qatf:8000 — the compose service
// name, resolved by Docker's DNS.
const API_TARGET = process.env.QATF_API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
    // Bind mounts on Windows and WSL do not deliver inotify events into the
    // container, so Vite's watcher never fires and "live reload" silently is
    // not. Polling is the documented escape hatch; 300ms is responsive without
    // spinning a core on a large tree.
    watch: { usePolling: true, interval: 300 },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
