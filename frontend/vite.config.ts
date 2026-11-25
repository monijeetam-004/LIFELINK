import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { cspPlugin } from "./vite-csp-plugin";

export default defineConfig(({ mode }) => {
  // Phase 28: read the same VITE_API_URL the app code itself reads via
  // import.meta.env (src/lib/api.ts) -- loadEnv here gives config-time
  // (Node) access to it so the CSP plugin can put the real configured API
  // origin in connect-src, matching production's own .env rather than a
  // hardcoded guess. Falls back to api.ts's own documented dev default so
  // an unset VITE_API_URL doesn't crash the build.
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const apiBaseUrl = env.VITE_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

  return {
    plugins: [react(), cspPlugin(apiBaseUrl)],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    server: {
      port: 5173,
    },
    build: {
      // Hardening: fonts are self-hosted and the production CSP is
      // `font-src 'self'`. Vite would otherwise inline font subsets under
      // 4 kB as `data:` URIs, which that policy (correctly) blocks -- found
      // by the Phase J journey E2E's "no CSP violations" check. Keep fonts
      // as real files; other small assets inline as before.
      assetsInlineLimit: (filePath: string) =>
        /\.(woff2?|ttf|otf|eot)$/i.test(filePath) ? false : undefined,
    },
  };
});
