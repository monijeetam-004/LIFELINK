---
name: Vital Precision
colors:
  surface: '#FFFFFF'
  surface-dim: '#d8dadc'
  surface-bright: '#f7f9fb'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f2f4f6'
  surface-container: '#eceef0'
  surface-container-high: '#e6e8ea'
  surface-container-highest: '#e0e3e5'
  on-surface: '#191c1e'
  on-surface-variant: '#5b403d'
  inverse-surface: '#2d3133'
  inverse-on-surface: '#eff1f3'
  outline: '#906f6c'
  outline-variant: '#e4beb9'
  surface-tint: '#bb171c'
  primary: '#b7131a'
  on-primary: '#ffffff'
  primary-container: '#db322f'
  on-primary-container: '#fffbff'
  inverse-primary: '#ffb4ac'
  secondary: '#005db7'
  on-secondary: '#ffffff'
  secondary-container: '#64a1ff'
  on-secondary-container: '#003670'
  tertiary: '#005da9'
  on-tertiary: '#ffffff'
  tertiary-container: '#0076d3'
  on-tertiary-container: '#fdfcff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#ffdad6'
  primary-fixed-dim: '#ffb4ac'
  on-primary-fixed: '#410002'
  on-primary-fixed-variant: '#93000d'
  secondary-fixed: '#d6e3ff'
  secondary-fixed-dim: '#a9c7ff'
  on-secondary-fixed: '#001b3d'
  on-secondary-fixed-variant: '#00468c'
  tertiary-fixed: '#d4e3ff'
  tertiary-fixed-dim: '#a4c9ff'
  on-tertiary-fixed: '#001c39'
  on-tertiary-fixed-variant: '#004884'
  background: '#f7f9fb'
  on-background: '#191c1e'
  surface-variant: '#e0e3e5'
  success: '#2E7D32'
  warning: '#FB8C00'
  border-subtle: '#E2E8F0'
  text-main: '#08090A'
  accent-linear: '#E4F222'
typography:
  headline-xl:
    fontFamily: Plus Jakarta Sans
    fontSize: 40px
    fontWeight: '700'
    lineHeight: 48px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Plus Jakarta Sans
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-lg-mobile:
    fontFamily: Plus Jakarta Sans
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 36px
  headline-md:
    fontFamily: Plus Jakarta Sans
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
  body-sm:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  label-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.01em
  label-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  unit: 8px
  container-max: 1440px
  gutter: 24px
  margin-desktop: 48px
  margin-tablet: 32px
  margin-mobile: 16px
---

## Brand & Style

The design system is engineered for **Vital Precision**, a high-stakes healthcare decision support platform. The brand personality is authoritative yet empathetic, balancing the urgency of medical intervention with the calm, systematic reliability of elite enterprise software.

The visual style is a synthesis of **Corporate Modern** and **Minimalism**, drawing heavily from the execution-oriented aesthetics of Linear and the polished clarity of Stripe. It prioritizes information density without sacrificing legibility, utilizing heavy whitespace to reduce cognitive load during critical decision-making moments. The interface feels "technical" but premium, utilizing subtle motion and high-fidelity typography to instill a sense of absolute reliability.

## Colors

The palette is anchored by **Medical Red**, reserved exclusively for primary calls to action, emergency indicators, and critical alerts. **Royal Blue** serves as the foundation for navigation and trust-building elements, providing a professional counterweight to the high-energy red.

The background uses a cool-toned neutral (`#F8FAFC`) to differentiate the page surface from the pure white (`#FFFFFF`) card containers. A specialized "Linear-inspired" accent (`#E4F222`) is used sparingly for focus indicators or new feature discovery to maintain a cutting-edge tech feel. Functional colors for success and warning follow standard medical conventions but are adjusted for high legibility against white surfaces.

## Typography

This design system utilizes **Plus Jakarta Sans** for headings to introduce a modern, slightly softer geometric feel that feels approachable, while **Inter** handles all functional and body text for maximum clarity and cross-platform consistency. 

Tight letter-spacing is applied to larger headlines to achieve that premium "Linear" look. Labels and metadata utilize uppercase styles and heavier weights to ensure hierarchical clarity in data-dense environments. For code snippets or medical IDs, a monospaced font should be substituted to ensure character distinction.

## Layout & Spacing

The system is built on a strict **8pt grid**, ensuring mathematical harmony across all components. We employ a **Fluid-Fixed Hybrid Grid**: content is contained within a 1440px max-width container for desktop readability but scales fluidly on smaller viewports.

Layouts should favor generous internal padding within cards (typically 24px or 32px) to prevent data from feeling cramped. Vertical rhythm is maintained by using multiples of 8px for all margins. Breakpoints are set at 640px (Mobile), 1024px (Tablet), and 1440px (Desktop).

## Elevation & Depth

Hierarchy is established through **Tonal Layering** and **Ambient Shadows**. The base background is the lowest level. White cards sit on top of this background, featuring a subtle `1px` border in `#E2E8F0` and a "soft-diffused" shadow: `0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05)`.

Interactive elements like cards or buttons should use **Hover Elevation**, where the shadow deepens and the element moves -2px on the Y-axis to simulate physical lift. Critical modals use a backdrop blur (Glassmorphism) of 8px to maintain context while focusing user attention.

## Shapes

The shape language is consistently **Rounded**, using an 8px (`0.5rem`) base radius for standard components like input fields and small buttons. Larger containers and cards use a 16px (`1rem`) radius to soften the professional aesthetic. This roundedness balances the "technical" nature of the platform with the "caring" nature of healthcare.

## Components

### Buttons
Primary buttons use a solid Medical Red fill with white text and a subtle inner-glow on top. Secondary buttons use the Royal Blue or a ghost-style outline. All buttons feature a 16px corner radius and a 200ms transition for hover states.

### Cards
Cards are the primary content vessel. They must have a pure white background, 16px corner radius, and a subtle light gray border. In dashboard views, cards should have a "hover elevation" state to indicate interactivity.

### Inputs & Forms
Input fields use a subtle light gray background (`#F1F5F9`) that turns white on focus with a 2px Royal Blue border. Errors are indicated with a Medical Red border and accompanying inline text.

### Chips & Badges
Used for status indicators (e.g., "Stable", "Critical"). Chips use a high-contrast background (e.g., Success Green background with white text) and a pill-shape (full rounding) to differentiate them from buttons.

### Icons
Use **Lucide-style** outline icons with a consistent 1.5px or 2px stroke width. Icons should be colored according to their functional context (e.g., a "Warning" icon in Warning Orange).