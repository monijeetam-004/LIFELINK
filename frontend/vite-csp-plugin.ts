import type { Plugin } from "vite";

/**
 * Phase 28 -- production-only Content-Security-Policy.
 *
 * Phase 23 deliberately left CSP OUT of the backend's security-headers
 * middleware (app/core/security_headers.py) with a documented reason: the
 * backend never serves this SPA's HTML at all (grep confirms no
 * StaticFiles/FileResponse route in app/main.py -- this is a pure JSON API,
 * and the frontend is served by a separate static host/CDN/reverse proxy),
 * so a CSP header/meta tag is architecturally this frontend build's
 * responsibility, not the backend's -- and at the time, the app's actual
 * external-origin footprint (fonts, map tiles) hadn't been inventoried, so
 * adding one blind risked silently breaking Leaflet or Google Fonts.
 *
 * This plugin closes that gap for the actual, inventoried external-origin
 * footprint. Production hardening (self-hosting) removed every external
 * asset host except the map tiles:
 *   - fonts (Inter, Plus Jakarta Sans, Material Symbols Outlined) are now
 *     bundled from npm (@fontsource-variable/*, material-symbols) and served
 *     from 'self' -- fonts.googleapis.com / fonts.gstatic.com are no longer
 *     allowed;
 *   - the 25 leftover lh3.googleusercontent.com mockup images (logo, the
 *     certificate seal, avatars, decorative backgrounds) were replaced by
 *     local SVGs under public/brand and public/illustrations --
 *     lh3.googleusercontent.com is no longer allowed;
 *   - https://*.tile.openstreetmap.org (Leaflet's default 'abc' tile
 *     subdomains, used by DonorMap.tsx's L.tileLayer) remains: map tiles
 *     are an external service by nature. Offline, the map shows no tiles;
 *     every other screen renders fully.
 *   - the configured API origin (VITE_API_URL), for XHR/fetch
 *
 * `apply: 'build'` is deliberate and load-bearing: Vite's dev server
 * (`npm run dev`) injects its own inline HMR client bootstrap and the React
 * Fast Refresh preamble as inline <script> tags directly into index.html on
 * every request -- a CSP without 'unsafe-inline'/a nonce would break HMR
 * outright, and loosening script-src to allow it would defeat the point of
 * shipping a CSP at all. Restricting this plugin to production builds means
 * local development is completely unaffected (dev already had no CSP,
 * unchanged), while `npm run build`'s output -- which never contains an
 * inline script; Vite's production output is always external hashed
 * `<script type="module" src="/assets/...">` tags -- gets a real,
 * `'unsafe-inline'`-free policy.
 */
export function cspPlugin(apiBaseUrl: string): Plugin {
  const connectSrcExtra = (() => {
    try {
      return new URL(apiBaseUrl).origin;
    } catch {
      // A relative VITE_API_URL (e.g. "/api", used when the frontend and
      // API share an origin behind one reverse proxy) has no distinct
      // origin to add -- 'self' below already covers that case.
      return null;
    }
  })();

  const directives = [
    "default-src 'self'",
    // Deliberately strict, no 'unsafe-inline'/'unsafe-eval': this is the
    // directive that actually matters for XSS (arbitrary script
    // execution), and Vite's production build never emits an inline
    // <script> -- verified by an actual headless-browser run against a
    // real `npm run build` output (see the Phase 28 report), which showed
    // zero script-src violations.
    "script-src 'self'",
    // NOT equally strict, and that is a deliberate, evidence-based choice,
    // not an oversight: the same headless-browser run showed 2000+ real
    // "Refused to apply inline style" violations the instant any page
    // rendered, from two genuine, unavoidable sources -- React's own
    // `style={{...}}` dynamic inline styles (used throughout this
    // codebase) and Leaflet's marker/tile positioning, which works by
    // writing `transform`/`left`/`top` directly to each element's `style`
    // attribute on every pan/zoom/drag (computed per-frame, so it cannot
    // be satisfied by a static hash or a build-time nonce). Removing
    // 'unsafe-inline' here would not tighten security meaningfully -- CSS
    // injection cannot execute arbitrary JavaScript in a modern browser --
    // it would just break the map and a large fraction of the UI. See the
    // Phase 28 report for the full before/after violation count.
    "style-src 'self' 'unsafe-inline'",
    "font-src 'self'",
    // Phase J: `data:` added -- Leaflet assigns its built-in 1x1 GIF data URI
    // (L.Util.emptyImageUrl) to tiles it cancels while panning/zooming; the
    // policy blocked it, logging a CSP violation on every map page (hospital
    // dashboard, donor map). An image data URI cannot execute script.
    "img-src 'self' data: https://*.tile.openstreetmap.org",
    `connect-src 'self'${connectSrcExtra ? ` ${connectSrcExtra}` : ""}`,
    // frame-ancestors is deliberately NOT included here: the CSP spec
    // requires it be delivered as a real HTTP response header -- a
    // <meta http-equiv="..."> CSP silently ignores it (confirmed via an
    // actual browser console warning during testing: "The Content
    // Security Policy directive 'frame-ancestors' is ignored when
    // delivered via a <meta> element"). Including a directive that does
    // nothing would be actively misleading in an audit document. Framing
    // protection for this SPA shell -- like X-Frame-Options, which
    // app/core/security_headers.py already documents as out of the
    // backend's reach since it never serves this HTML -- must come from a
    // real header set by whatever static host/CDN/reverse proxy serves
    // this dist/ output in production; see the Phase 28 report's
    // deployment-checklist section for the exact header to add there.
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
  ];
  const csp = directives.join("; ") + ";";

  return {
    name: "lifelink-production-csp",
    apply: "build",
    // Phase 28: `order: 'pre'` + inserting right after the opening <head>
    // tag (not just before </head>) is load-bearing, not cosmetic -- a CSP
    // delivered via <meta> only governs elements that appear AFTER it in
    // the parsed document (HTML spec / browser behavior), so it must be
    // the first thing in <head>, ahead of the bundled stylesheet <link> and
    // Vite's own injected <script type="module">/<link rel="stylesheet">
    // tags -- otherwise those resource loads would be parsed (and
    // fetched) before the policy takes effect at all, defeating the point.
    transformIndexHtml: {
      order: "pre",
      handler(html) {
        const metaTag = `<meta http-equiv="Content-Security-Policy" content="${csp}" />`;
        return html.replace(/<head(\s[^>]*)?>/i, (match) => `${match}\n    ${metaTag}`);
      },
    },
  };
}
