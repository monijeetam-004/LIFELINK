# LifeLink Frontend

> See the top-level [`../README.md`](../README.md) for current status — all
> 24 screens across five portals are implemented and routed, wired to the
> real backend wherever an endpoint exists. The notes below predate that and
> describe only the original 13-screen Stitch UI port; keep reading for
> accurate build/dev mechanics.

> **Update (Phase 1 — Auth):** authentication is now wired to the real backend
> (`../backend`). `src/lib/api.ts` (typed axios client, `VITE_API_URL`,
> cookie credentials) + `src/lib/auth.tsx` (`AuthProvider`/`useAuth`) back the
> `AuthPage`, the new `VerifyEmail` / `ForgotPassword` / `ResetPassword` pages,
> the `RequireAuth` route guards and the `AccountMenu` sign-out control. Copy
> `.env.example` → `.env` and run the backend before `npm run dev`. Everything
> below describes the original Stitch UI port; the dashboards' data is still
> static pending later phases.

---

## Original notes — Phase 1 (UI port from Stitch)

This is the React/TypeScript/Vite/Tailwind conversion of the Google Stitch
export you provided (`stitch_lifelink_decision_support_system.zip`). This
phase covers **frontend only** — no backend, no database yet (see "What's
next" below).

## What's in here

- **Design system** (`tailwind.config.ts`, `src/index.css`): every color,
  spacing token, font size, and custom animation/utility class from the
  Stitch export's inline `tailwind.config` blocks and `<style>` tags,
  consolidated into one canonical theme. The 13 screens you exported each
  came from a separate Stitch generation and carried slightly different
  auto-generated Material-3 palettes; `patient_dashboard`'s palette was used
  as the single source of truth so the whole app looks consistent, and every
  page-specific custom CSS rule (glass panels, shimmer, pulse rings, the
  certificate's guilloché pattern, etc.) was merged in rather than dropped.
- **13 pages** (`src/pages/*.tsx`) — a 1:1 structural conversion of every
  `code.html` in the export (class → className, style strings → style
  objects, comments stripped, stray `<`/`>` in text escaped for JSX). Markup
  and Tailwind classes are untouched — nothing was redesigned or simplified.
- **Shader background** (`src/components/ShaderBackground.tsx`) — the
  WebGL "medical pulse" canvas from `shader/code.html`, ported to a React
  component with the same GLSL shaders and mouse-reactive uniform.
- **Router** (`src/App.tsx`) — every page wired to a route, plus a
  dev-only `/` index listing them (temporary — swap for the real Landing
  page later).

| Route | Screen |
|---|---|
| `/recommendation` | Donor Recommendation (search results) |
| `/patient` | Patient Dashboard |
| `/donor`, `/donor/mobile` | Donor Dashboard (desktop / mobile reference) |
| `/hospital` | Hospital Dashboard |
| `/admin/verification` | Admin Verification Portal |
| `/decision-transparency` | Decision Transparency — Why Not Others |
| `/mathematical-model` | Mathematical Model (set-theory pipeline) |
| `/graph-engine` | Graph Decision Engine |
| `/simulation` | Emergency Simulation Mode |
| `/research-mode` | Research Mode |
| `/analytics` | Analytics Command Center |
| `/certificate` | Donation Certificate |

## Running it

```bash
cd frontend
npm install
npm run dev
```

This container has no network access, so `npm install` has **not** been run
here — do it on your machine. `package.json` pins the stack requested in the
brief (React 19, Vite, TypeScript, Tailwind, React Router, TanStack Query,
React Hook Form, Axios, Framer Motion, React Flow, Leaflet, Lucide, Recharts).

Every `.tsx` file was type/syntax-checked with `tsc` before delivery, so
`npm run build` should compile cleanly once dependencies are installed.

## What's next (not in this phase)

- **Backend**: Flask API, JWT auth, the discrete-math engine (set filtering,
  Dijkstra, donation readiness scoring, bipartite matching).
- **Database**: MySQL schema + seed data.
- **Pages not in the Stitch export**: Landing, Auth (login/register/OTP),
  Search, Search Results (distinct from Donor Recommendation), Donor
  Profile, Hospital Profile, Blood Compatibility, Notifications, Settings,
  Help Center, About. These need to be designed in the same visual language
  as the 13 ported screens.
- **Wiring**: buttons/links currently render but don't call any API —
  that lands once the backend exists. React Flow, Leaflet, and Recharts are
  in `package.json` but not yet used inside the graph/analytics pages; the
  static SVG/HTML from Stitch is preserved as-is for now.
- **Refactor pass**: the sidebar/topbar/footer markup is duplicated across
  the dashboard pages (kept that way deliberately in this pass to guarantee
  pixel fidelity per page). Once routing/auth exist, that's worth extracting
  into a shared `<DashboardLayout>`.
