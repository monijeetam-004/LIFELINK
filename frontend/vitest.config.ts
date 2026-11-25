import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

/**
 * Phase 32 -- a dedicated Vitest config, deliberately SEPARATE from
 * vite.config.ts (the production dev-server/build config).
 *
 * Why separate rather than merged into vite.config.ts:
 *   - vite.config.ts calls `loadEnv` and feeds the real VITE_API_URL into a
 *     custom CSP plugin (`cspPlugin`) that's only meaningful for the
 *     served/built app -- component tests never serve a page or need a CSP
 *     header, and mixing that config-time logic into the test config would
 *     add a real (if small) risk of `npm run build`/`npm run dev` behavior
 *     changing as a side effect of a testing change.
 *   - Keeping this config in its own file means `npm run build` and
 *     `npm run dev` literally never read this file, so they cannot regress
 *     because of anything added here.
 *
 * What this DOES intentionally share with vite.config.ts: the same "@" path
 * alias (so test files can import components exactly the way the app does)
 * and the React plugin (for JSX/Fast Refresh-free transform in tests).
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    // Component/unit tests only -- Playwright's own e2e/ specs are a
    // completely separate test runner and must never be picked up here.
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["node_modules", "dist", "e2e"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/**/*.test.{ts,tsx}",
        "src/test/**",
        "src/main.tsx",
        "src/vite-env.d.ts",
      ],
    },
  },
});
