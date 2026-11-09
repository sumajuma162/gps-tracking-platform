import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const backend = process.env.VITE_DEV_BACKEND ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": backend,
      "/health": backend,
      "/readiness": backend,
      "/ws": { target: backend.replace("http", "ws"), ws: true },
    },
  },
  test: { environment: "jsdom", globals: true, setupFiles: ["src/setupTests.ts"] },
});
