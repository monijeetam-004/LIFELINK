---
name: Clinical Precision & Heritage
colors:
  surface: '#f8f9fb'
  surface-dim: '#d8dadc'
  surface-bright: '#f8f9fb'
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
  tertiary: '#2858b2'
  on-tertiary: '#ffffff'
  tertiary-container: '#4671cd'
  on-tertiary-container: '#fefcff'
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
  tertiary-fixed: '#d9e2ff'
  tertiary-fixed-dim: '#b0c6ff'
  on-tertiary-fixed: '#001945'
  on-tertiary-fixed-variant: '#00429c'
  background: '#f8f9fb'
  on-background: '#191c1e'
  surface-variant: '#e0e3e5'
typography:
  display-lg:
    fontFamily: Manrope
    fontSize: 48px
    fontWeight: '800'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Manrope
    fontSize: 32px
    fontWeight: '700'
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
    fontWeight: '700'
    lineHeight: 16px
    letterSpacing: 0.05em
  cert-name:
    fontFamily: Noto Serif
    fontSize: 42px
    fontWeight: '600'
    lineHeight: 52px
    letterSpacing: 0.01em
  cert-body:
    fontFamily: Noto Serif
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  base: 8px
  xs: 4px
  sm: 12px
  md: 24px
  lg: 40px
  xl: 64px
  gutter: 24px
  margin-mobile: 16px
  margin-desktop: 32px
---

## Brand & Style

The design system is engineered for a high-stakes medical environment where data accuracy and institutional trust are paramount. It balances a **Corporate Modern** UI for analytics with a **Sophisticated Editorial** style for certification. 

The aesthetic is characterized by clinical cleanliness, high-density information displays, and "glass-on-steel" surfaces. For the analytics dashboard, the focus is on utility, using light-gray backgrounds to reduce eye strain during prolonged data monitoring. For certifications, the style shifts toward a premium, authoritative feel, utilizing high-contrast serif typography and intricate decorative elements to signify achievement and official validation.

The target audience includes healthcare administrators, laboratory technicians, and certified professionals who require a UI that feels both technologically advanced and medically reliable.

## Colors

This design system utilizes a high-contrast palette optimized for legibility in clinical settings. 
- **Primary (Medical Red):** Reserved for critical status indicators, emergency actions, and branding accents.
- **Secondary (Royal Blue):** Used for primary navigation, call-to-action buttons, and interactive elements.
- **Surface Palette:** Employs a range of cool grays (`#F5F7F9` to `#CFD8DC`) to create distinct content areas without heavy borders.
- **Data Visualization:** A specific sub-palette is defined for charts. Use high-contrast steps to ensure accessibility. Heatmaps must transition from a desaturated cool blue to the deep Royal Blue to indicate density/frequency without using red (which is reserved for errors).

## Typography

The system employs a dual-personality typographic scale. 
1. **The Analytics Interface:** Uses **Manrope** for headers to provide a modern, technical feel, and **Inter** for all body text and data tables due to its superior legibility at small sizes and high x-height.
2. **The Certification Layer:** Introduces **Noto Serif** for professional credentials. This serif face conveys a sense of history and "earned" authority.

All data labels should use the `label-caps` style to differentiate metadata from user-generated content. Headlines above 32px should scale down to 24px on mobile devices to maintain layout integrity.

## Layout & Spacing

This design system is built on a strict **8pt spacing grid**. All dimensions, padding, and margins must be multiples of 8.

- **Grid:** Use a 12-column fluid grid for the analytics dashboard with 24px gutters. For data-heavy views, a 4-column "Sidebar + Main" layout is preferred.
- **Density:** The UI defaults to a "Compact" density to allow for maximum data visualization. 
- **Certification Layout:** Certificates should use a fixed aspect ratio (ISO 216 A4) with a dedicated safe margin of 64px on all sides to allow for printing and framing. 
- **Breakpoints:**
  - Mobile: < 600px (1-column)
  - Tablet: 600px - 1024px (6-column)
  - Desktop: > 1024px (12-column)

## Elevation & Depth

Visual hierarchy is managed through **Tonal Layers** and crisp, low-opacity shadows. 
- **Background:** The base layer is `#F5F7F9`.
- **Containers:** Dashboard widgets and cards use a white background with a 1px border (`#E0E4E8`) and no shadow.
- **Overlays:** Modals and dropdowns use a sharp elevation (8% opacity black shadow, 16px blur) to separate them from the data grid.
- **Certification Specifics:** Use "Tactile" depth. Digital certificates should feature a subtle light-source from the top-left, creating an embossed effect for seal elements.

## Shapes

The shape language is **Soft (0.25rem)**, reflecting a clinical and precise atmosphere. 
- **Buttons and Inputs:** Use a 4px radius (`rounded-md`).
- **Data Points:** Markers in line charts should be circular, while bars in histograms should have 0px top-rounding to ensure accurate value representation.
- **Certificates:** Borders should be sharp (0px) to mimic traditional cardstock, but internal "seals" or stamps may be perfectly circular.

## Components

- **Buttons:** Primary buttons use Royal Blue with white text. "Emergency" buttons use Medical Red. All buttons have a subtle 1px inset top border for a slightly tactile feel.
- **Data Tables:** Use alternating row stripes (Zebra striping) using `#F9FAFB`. Headers are sticky with a 2px Royal Blue bottom border.
- **Status Indicators:** Use a "Pill" shape with 10% opacity background of the status color and 100% opacity text (e.g., Success is a light green pill with dark green text).
- **Cards:** Dashboard cards must include a "Header" area with a subtle 1px bottom border and an "Actions" area in the footer.
- **Certificate Borders:** Define a "Double-Frame" border—a thick 4pt outer line and a thin 1pt inner line in Royal Blue. 
- **Security Watermark:** The LifeLink logo should be repeated in a 45-degree tiled pattern at 3% opacity across the background of all certification PDF exports.
- **Input Fields:** Use a "Clinical" style—white background, 1px gray border, and a blue focus ring. Labels always sit above the field.