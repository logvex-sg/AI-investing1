import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In the desktop build the shell injects the backend port; in the browser dev
// server the request is proxied to the local API. Either way the frontend only
// ever talks to one origin.
const target = process.env.ECOSYSTEM_API_TARGET || "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  // Relative asset paths are required for the custom-protocol (tauri://) scheme.
  base: "./",
  server: {
    port: 5173,
    host: true,
    strictPort: true,
    // Dev tunnels (e.g. *.prod-runtime.all-hands.dev) proxy the dev server by
    // hostname; Vite otherwise rejects unknown Host headers.
    allowedHosts: [".prod-runtime.all-hands.dev"],
    proxy: {
      "/api": {
        target,
        changeOrigin: true,
        // Server-Sent Events must not be buffered by the proxy.
        configure: (proxy) => {
          proxy.on("proxyRes", (proxyRes) => {
            if ((proxyRes.headers["content-type"] || "").includes("text/event-stream")) {
              proxyRes.headers["x-accel-buffering"] = "no";
            }
          });
        },
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
