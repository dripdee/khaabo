import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    // Proxy keeps the browser same-origin in dev, so no CORS preflight and the
    // production API base URL can be swapped without touching component code.
    proxy: {
      "/api": {
        target: process.env.VITE_PROXY_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    target: "es2020",
    sourcemap: true,
    rollupOptions: {
      output: {
        // Leaflet and the query/router layer change far less often than app code;
        // splitting them keeps the cache warm across deploys.
        manualChunks: {
          leaflet: ["leaflet", "react-leaflet", "leaflet.markercluster"],
          vendor: ["react", "react-dom", "react-router-dom", "@tanstack/react-query"],
          motion: ["framer-motion"],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
