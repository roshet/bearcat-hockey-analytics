/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API (`bca serve`) runs on :8000; the dev server proxies /api to it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
    // `bca export static` swaps public/data in whole; a watcher holding it open makes
    // Windows refuse the swap. The data is read per request, so nothing needs watching.
    watch: { ignored: ["**/public/data/**", "**/public/data.*/**"] },
  },
  test: {
    environment: "jsdom",
    // Read as text by the credit test (the print footer must match the on-screen credit).
    css: { include: [/index\.css/] },
  },
});
