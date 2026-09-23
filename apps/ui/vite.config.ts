import { defineConfig } from "vitest/config";
import { loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const rawPort = loadEnv(mode, ".", "NEMO_").NEMO_SERVER_PORT || "18765";
  const serverPort = Number(rawPort);
  if (!/^[0-9]+$/.test(rawPort) || serverPort < 1 || serverPort > 65535) {
    throw new Error("NEMO_SERVER_PORT must be an integer from 1 to 65535");
  }
  return {
    plugins: [react()],
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": {
          target: `http://127.0.0.1:${serverPort}`,
          changeOrigin: false,
          rewrite: (path) => path.replace(/^\/api/, ""),
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.ts",
    },
  };
});
