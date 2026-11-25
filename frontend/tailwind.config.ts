import type { Config } from "tailwindcss";

// Design tokens: "LifeLink Clinical Pulse" — the Google Stitch design system used for
// the 24-screen implementation (see design_export/lifelink_clinical_pulse/DESIGN.md).
// Values below are ported 1:1 from that spec. A handful of legacy token names from the
// original Phase-1 Stitch theme (success/warning/border-subtle/text-main/accent-linear,
// plus a few M3 "fixed"/"tertiary" roles not present in the new spec) are kept as-is so
// existing pages that already reference them keep rendering identically.
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // ---- LifeLink Clinical Pulse (DESIGN.md) — canonical ----
        surface: "#f7f9fb",
        "surface-dim": "#d8dadc",
        "surface-bright": "#f7f9fb",
        "surface-container-lowest": "#ffffff",
        "surface-container-low": "#f2f4f6",
        "surface-container": "#eceef0",
        "surface-container-high": "#e6e8ea",
        "surface-container-highest": "#e0e3e5",
        "on-surface": "#191c1e",
        "on-surface-variant": "#5b403d",
        "inverse-surface": "#2d3133",
        "inverse-on-surface": "#eff1f3",
        outline: "#906f6c",
        "outline-variant": "#e4beb9",
        "surface-tint": "#bb171c",
        primary: "#8e000d",
        "on-primary": "#ffffff",
        "primary-container": "#b7131a",
        "on-primary-container": "#ffc7c1",
        "inverse-primary": "#ffb4ac",
        secondary: "#005db7",
        "on-secondary": "#ffffff",
        "secondary-container": "#64a1ff",
        "on-secondary-container": "#003670",
        tertiary: "#593d3b",
        "on-tertiary": "#ffffff",
        "tertiary-container": "#735452",
        "on-tertiary-container": "#f3cbc7",
        error: "#ba1a1a",
        "on-error": "#ffffff",
        "error-container": "#ffdad6",
        "on-error-container": "#93000a",
        "primary-fixed": "#ffdad6",
        "primary-fixed-dim": "#ffb4ac",
        "on-primary-fixed": "#410002",
        "on-primary-fixed-variant": "#93000d",
        "secondary-fixed": "#d6e3ff",
        "secondary-fixed-dim": "#a9c7ff",
        "on-secondary-fixed": "#001b3d",
        "on-secondary-fixed-variant": "#00468c",
        "tertiary-fixed": "#ffdad6",
        "tertiary-fixed-dim": "#e5bdba",
        "on-tertiary-fixed": "#2b1614",
        "on-tertiary-fixed-variant": "#5c403d",
        background: "#f7f9fb",
        "on-background": "#191c1e",
        "surface-variant": "#e0e3e5",
        // ---- Legacy Phase-1 roles kept for backward compatibility ----
        // Phase 27: both values below were replaced with darker shades of
        // the same hue -- NOT a redesign, a WCAG 2.1 AA color-contrast fix.
        // Real axe-core measurements (see LIFELINK_PHASE_27_ACCESSIBILITY_
        // SCALABILITY_AUDIT.md) showed the original `warning` (#FB8C00)
        // failed contrast in EVERY context it's used in this app: 2.37:1 on
        // white, 2.25:1 on the page background, 2.08:1 even against its own
        // `bg-warning/15` tint -- all far below the 4.5:1 AA text minimum
        // (and below the 3:1 UI-component minimum too). `success` (#2E7D32)
        // passed on a plain white background (5.13:1) but failed on its own
        // `bg-success/15` tint (4.21:1, just under 4.5:1) -- the same
        // warning-badge/pill pattern used throughout the app. Since every
        // route in this codebase reaches these colors through the
        // `warning`/`success` Tailwind tokens (never a raw hex), fixing the
        // two token values here is the single, coherent, lowest-risk fix
        // that resolves every real instance at once, instead of hand-editing
        // each of the dozens of components that reference them (which would
        // both risk missing some and constitute the unnecessary
        // per-component redesign this phase was told to avoid). Verified
        // by direct WCAG relative-luminance computation, both new values
        // pass 4.5:1+ in every context the old ones failed:
        //   warning #8A4B00: 6.80:1 on white / 6.45:1 on page bg / 5.41:1 on own /15 tint
        //   success #256427: 7.17:1 on white / 5.70:1 on own /15 tint
        success: "#256427",
        warning: "#8A4B00",
        "border-subtle": "#E2E8F0",
        "text-main": "#08090A",
        "accent-linear": "#E4F222",
      },
      borderRadius: {
        xs: "0.25rem",
        sm: "0.5rem",
        DEFAULT: "0.5rem",
        md: "0.75rem",
        lg: "1rem",
        xl: "1.5rem",
        full: "9999px",
      },
      spacing: {
        "margin-desktop": "48px",
        "margin-mobile": "16px",
        "margin-tablet": "32px",
        "container-max": "1440px",
        gutter: "24px",
        "gutter-mobile": "16px",
        "gutter-tablet": "24px",
        "gutter-desktop": "24px",
        unit: "8px",
        "space-1": "4px",
        "space-2": "8px",
        "space-3": "12px",
        "space-4": "16px",
        "space-5": "20px",
        "space-6": "24px",
        "space-8": "32px",
        "space-10": "40px",
        "space-12": "48px",
        "space-16": "64px",
        "space-20": "80px",
        // QA fix: tokens the Stitch-exported pages use but the config never
        // defined (values from public/reference/DESIGN-*.md). Without them
        // Tailwind generated no CSS, so spacing silently collapsed on
        // Analytics, Research Mode, Verify Certificate and others.
        xs: "4px",
        base: "8px",
        sm: "12px",
        md: "24px",
        lg: "40px",
        xl: "64px",
        "4.5": "1.125rem",
        "space-2.5": "10px",
        "container-margin-mobile": "20px",
        "container-margin-desktop": "40px",
        safe: "env(safe-area-inset-bottom, 0px)",
      },
      fontFamily: {
        "body-lg": ["Inter Variable", "Inter", "sans-serif"],
        "body-md": ["Inter Variable", "Inter", "sans-serif"],
        "body-sm": ["Inter Variable", "Inter", "sans-serif"],
        "label-lg": ["Inter Variable", "Inter", "sans-serif"],
        "label-md": ["Inter Variable", "Inter", "sans-serif"],
        "label-sm": ["Inter Variable", "Inter", "sans-serif"],
        "data-mono": ["Inter Variable", "Inter", "sans-serif"],
        "label-caps": ["Inter Variable", "Inter", "sans-serif"],
        "title-sm": ["Plus Jakarta Sans Variable", "Plus Jakarta Sans", "sans-serif"],
        "data-numeric": ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
        "algorithm-tag": ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
        "display-lg": ["Plus Jakarta Sans Variable", "Plus Jakarta Sans", "sans-serif"],
        "headline-lg": ["Plus Jakarta Sans Variable", "Plus Jakarta Sans", "sans-serif"],
        "headline-md": ["Plus Jakarta Sans Variable", "Plus Jakarta Sans", "sans-serif"],
        "headline-sm": ["Plus Jakarta Sans Variable", "Plus Jakarta Sans", "sans-serif"],
        "headline-xl": ["Plus Jakarta Sans Variable", "Plus Jakarta Sans", "sans-serif"],
        "title-lg": ["Plus Jakarta Sans Variable", "Plus Jakarta Sans", "sans-serif"],
        "title-md": ["Plus Jakarta Sans Variable", "Plus Jakarta Sans", "sans-serif"],
      },
      fontSize: {
        "display-lg": ["48px", { lineHeight: "56px", letterSpacing: "-0.02em", fontWeight: "700" }],
        "headline-lg": ["32px", { lineHeight: "40px", letterSpacing: "-0.01em", fontWeight: "700" }],
        "headline-md": ["24px", { lineHeight: "32px", letterSpacing: "-0.005em", fontWeight: "600" }],
        "headline-sm": ["20px", { lineHeight: "28px", fontWeight: "600" }],
        "headline-xl": ["40px", { lineHeight: "48px", letterSpacing: "-0.02em", fontWeight: "700" }],
        "title-lg": ["18px", { lineHeight: "24px", fontWeight: "600" }],
        "title-md": ["16px", { lineHeight: "24px", fontWeight: "600" }],
        "body-lg": ["16px", { lineHeight: "24px", fontWeight: "400" }],
        "body-md": ["14px", { lineHeight: "20px", fontWeight: "400" }],
        "body-sm": ["12px", { lineHeight: "16px", fontWeight: "400" }],
        "label-lg": ["14px", { lineHeight: "20px", letterSpacing: "0.01em", fontWeight: "600" }],
        "label-md": ["12px", { lineHeight: "16px", letterSpacing: "0.02em", fontWeight: "600" }],
        "label-sm": ["11px", { lineHeight: "14px", letterSpacing: "0.04em", fontWeight: "500" }],
        "data-mono": ["13px", { lineHeight: "18px", letterSpacing: "0.02em", fontWeight: "500" }],
        "label-caps": ["12px", { lineHeight: "16px", letterSpacing: "0.05em", fontWeight: "600" }],
        "title-sm": ["14px", { lineHeight: "20px", fontWeight: "600" }],
        "data-numeric": ["14px", { lineHeight: "20px", fontWeight: "500" }],
        "algorithm-tag": ["11px", { lineHeight: "14px", fontWeight: "700" }],
        "headline-md-mobile": ["28px", { lineHeight: "36px", fontWeight: "600" }],
      },
      maxWidth: {
        "container-max": "1440px",
      },
      boxShadow: {
        "level-1": "0px 2px 8px -2px rgba(25, 28, 30, 0.04), 0px 1px 3px 0px rgba(183, 19, 26, 0.02)",
        "level-2": "0px 8px 24px -4px rgba(25, 28, 30, 0.08), 0px 2px 6px -1px rgba(183, 19, 26, 0.04)",
        "level-3": "0px 16px 40px -8px rgba(25, 28, 30, 0.16), 0px 4px 12px 0px rgba(183, 19, 26, 0.06)",
        xs: "0 1px 2px 0 rgba(0, 0, 0, 0.04)",
        soft: "0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05)",
      },
    },
  },
  plugins: [require("@tailwindcss/forms"), require("@tailwindcss/container-queries")],
} satisfies Config;
