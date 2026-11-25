---
name: LifeLink Research & Care
colors:
  surface: '#f7f9fb'
  surface-dim: '#d8dadc'
  surface-bright: '#f7f9fb'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f2f4f6'
  surface-container: '#eceef0'
  surface-container-high: '#e6e8ea'
  surface-container-highest: '#e0e3e5'
  on-surface: '#191c1e'
  on-surface-variant: '#434656'
  inverse-surface: '#2d3133'
  inverse-on-surface: '#eff1f3'
  outline: '#737688'
  outline-variant: '#c3c5d9'
  surface-tint: '#004ced'
  primary: '#003ec7'
  on-primary: '#ffffff'
  primary-container: '#0052ff'
  on-primary-container: '#dfe3ff'
  inverse-primary: '#b7c4ff'
  secondary: '#505f76'
  on-secondary: '#ffffff'
  secondary-container: '#d0e1fb'
  on-secondary-container: '#54647a'
  tertiary: '#464e64'
  on-tertiary: '#ffffff'
  tertiary-container: '#5e667d'
  on-tertiary-container: '#dde4ff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#dde1ff'
  primary-fixed-dim: '#b7c4ff'
  on-primary-fixed: '#001452'
  on-primary-fixed-variant: '#0038b6'
  secondary-fixed: '#d3e4fe'
  secondary-fixed-dim: '#b7c8e1'
  on-secondary-fixed: '#0b1c30'
  on-secondary-fixed-variant: '#38485d'
  tertiary-fixed: '#dae2fd'
  tertiary-fixed-dim: '#bec6e0'
  on-tertiary-fixed: '#131b2e'
  on-tertiary-fixed-variant: '#3f465c'
  background: '#f7f9fb'
  on-background: '#191c1e'
  surface-variant: '#e0e3e5'
typography:
  display-lg:
    fontFamily: Manrope
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Manrope
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
  headline-sm:
    fontFamily: Manrope
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  label-caps:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.05em
  data-numeric:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
  algorithm-tag:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '700'
    lineHeight: 14px
  headline-md-mobile:
    fontFamily: Manrope
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 36px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  unit: 4px
  container-margin-mobile: 20px
  container-margin-desktop: 40px
  gutter: 16px
  touch-target-min: 44px
---

## Brand & Style

The design system operates on a dual-pathway brand personality: **Clinical Precision** for researchers and **Premium Empathy** for donors. The target audience includes medical professionals requiring data density and layperson donors requiring high-trust, low-friction interactions.

The design style is **Modern Corporate with Glassmorphic accents**. It utilizes heavy whitespace to reduce cognitive load in high-stakes healthcare scenarios. The "Research Mode" introduces a sophisticated layer of technical transparency through "Professor Mode" elements—utilizing monospaced data counters and algorithmic labels to validate the platform's computational integrity. The emotional response is one of calm authority, reliability, and cutting-edge medical advancement.

## Colors

The palette is anchored by a deep **Clinical Blue** (Primary) that signals trust and established healthcare standards. 

- **Primary:** Used for main actions and donor-facing "LifeLink" interactions.
- **Secondary/Tertiary:** Slate and Navy tones provide the structural foundation for "Research Mode" interfaces.
- **Urgency Color:** A specific Crimson used sparingly for critical donor alerts and time-sensitive matching.
- **Algorithm Accent:** A technical Violet used exclusively for "Professor Mode" labels and mathematical complexity indicators to distinguish academic metadata from functional UI.

## Typography

This design system utilizes a tri-font strategy to separate intent:
- **Manrope** (Headlines): Provides a modern, refined, and balanced feel for primary navigation and titles.
- **Inter** (Body): A systematic, utilitarian typeface chosen for maximum legibility in dense medical documentation.
- **JetBrains Mono** (Research/Data): Specifically for the "Research Mode" components. It provides the technical "Professor Mode" aesthetic, ensuring that algorithmic complexity labels and high-precision counters are visually distinct from patient data.

Mobile typography scales down headlines for readability while maintaining generous line heights to ensure touch-friendly scanning.

## Layout & Spacing

The layout follows a **Fluid Grid** model with strict adherence to an 8px spacing rhythm for standard elements and 4px for "Research Mode" data density.

- **Donor Experience (Mobile):** Prioritizes large margins (20px) and substantial touch targets (min 44px). Buttons and urgency indicators must span the full width of their containers to facilitate one-handed use during critical moments.
- **Research Experience (Desktop):** Increases density. Uses a 12-column grid to accommodate sidebar navigation, data visualization, and the "Professor Mode" tooltip overlays.
- **Urgency Layouts:** Use centered, high-contrast layouts with minimal distractions to drive immediate action.

## Elevation & Depth

Visual hierarchy is established through **Tonal Layers** and **Subtle Glassmorphism**:

1.  **Base Layer:** The neutral background (F8FAFC).
2.  **Surface Layer:** White containers with a 1px border (#E2E8F0) and no shadow for a flat, clinical look.
3.  **Research Overlays:** Tooltips and "Professor Mode" labels use a subtle backdrop blur (8px) and 80% opacity white fill to sit "above" the data without obscuring context.
4.  **Critical Alerts:** High-elevation shadows (Soft, 12% opacity black, 16px blur) are reserved strictly for donor urgency alerts and critical action modals to draw immediate focus.

## Shapes

The design system employs **Rounded (2)** corners (0.5rem / 8px) to soften the clinical nature of the product while maintaining professional structure.

- **Standard Components:** 8px radius (Buttons, Cards, Inputs).
- **Large Containers:** 16px radius (Modals, Section blocks).
- **Research Tags:** 4px radius (Specific to algorithm labels and high-precision counters) to suggest a more rigid, mathematical precision.
- **Urgency Indicators:** Maintain the standard 8px radius but use thicker 2px borders to emphasize importance.

## Components

### Research & Professor Mode
- **High-Precision Data Counters:** Use `data-numeric` typography. Display with 4 decimal places. Frame in a subtle grey box with a light monospace label above.
- **Algorithm Complexity Labels:** Rendered in `algorithm-tag` typography using the `algorithm_accent_hex` color. Style as small, low-padding chips (e.g., `O(n log n)`).
- **Professor Mode Tooltips:** Triggered by "info" icons next to technical data. Use glassmorphic backgrounds with `body-md` text. They provide educational context on *why* a specific algorithm was used for the match.

### Donor Experience (Mobile)
- **Urgency Indicators:** Top-aligned banners in `urgency_color_hex` with white `label-caps` text. Pulse animation allowed for 30 seconds upon initial view.
- **Action Buttons:** Large (56px height) with `primary_color_hex`. Use `headline-sm` for button labels to ensure maximum legibility.
- **Input Fields:** High-contrast borders (1.5px) when focused. Labels always visible (never placeholder-only) to maintain accessibility standards in high-stress scenarios.

### Global
- **Cards:** White background, 1px border, 8px radius. In Research Mode, cards include a "Technical Detail" toggle that reveals the complexity labels and counters.