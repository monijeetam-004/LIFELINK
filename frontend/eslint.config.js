// Flat config for ESLint 9 (mirrors the intent of the legacy .eslintrc.cjs).
import js from "@eslint/js";
import globals from "globals";
import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";

export default [
  { ignores: ["dist/**", "node_modules/**"] },
  js.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsParser,
      parserOptions: { ecmaVersion: "latest", sourceType: "module" },
      globals: { ...globals.browser, ...globals.es2022 },
    },
    plugins: { "@typescript-eslint": tsPlugin },
    rules: {
      ...tsPlugin.configs.recommended.rules,
      // TypeScript itself resolves references; core no-undef is redundant and
      // misfires on type-only identifiers like `React.FormEvent`.
      "no-undef": "off",
      "@typescript-eslint/no-explicit-any": "off",
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // Build/config files legitimately use Node's require() for plugins.
    files: ["*.config.{js,ts}", "vite.config.ts", "tailwind.config.ts", "postcss.config.js"],
    languageOptions: { globals: { ...globals.node } },
    rules: { "@typescript-eslint/no-require-imports": "off" },
  },
  {
    // Node-run tooling scripts (e.g. the CI npm-audit gate).
    files: ["scripts/**/*.mjs"],
    languageOptions: { globals: { ...globals.node } },
  },
];
