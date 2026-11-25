/**
 * Hardening -- every image and font the app shows is self-hosted.
 * Guards against re-introducing the leftover mockup assets on
 * lh3.googleusercontent.com or the Google Fonts stylesheet, and checks the
 * production CSP no longer allows those hosts. Map tiles
 * (*.tile.openstreetmap.org) are the only external asset origin allowed.
 */
import { describe, expect, it } from "vitest";
import indexHtml from "../../index.html?raw";
import { cspPlugin } from "../../vite-csp-plugin";

const sources = import.meta.glob("../**/*.{ts,tsx,css}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

function builtCsp(): string {
  const plugin = cspPlugin("http://localhost:8000");
  const hook = plugin.transformIndexHtml as { handler: (html: string) => string };
  const out = hook.handler("<html><head></head><body></body></html>");
  const m = out.match(/content="([^"]+)"/);
  if (!m) throw new Error("no CSP meta tag");
  return m[1];
}

describe("self-hosted assets", () => {
  it("no source file or index.html references an external image/font host", () => {
    const offenders = Object.entries({ ...sources, "index.html": indexHtml })
      .filter(([name]) => !name.endsWith("selfHostedAssets.test.ts"))
      .filter(([, text]) => /googleusercontent\.com|fonts\.googleapis\.com|fonts\.gstatic\.com/.test(text))
      .map(([name]) => name);
    expect(offenders).toEqual([]);
    expect(Object.keys(sources).length).toBeGreaterThan(50); // the glob really scanned the tree
  });

  it("every <img src> literal points at a local path", () => {
    const external: string[] = [];
    for (const [name, text] of Object.entries(sources)) {
      for (const m of text.matchAll(/<img[^>]*\ssrc="([^"]+)"/g)) {
        if (/^https?:\/\//.test(m[1])) external.push(`${name}: ${m[1]}`);
      }
    }
    expect(external).toEqual([]);
  });

  it("the production CSP allows only 'self' for fonts and only map tiles as an external image host", () => {
    const csp = builtCsp();
    expect(csp).toContain("font-src 'self';");
    expect(csp).toContain("img-src 'self' data: https://*.tile.openstreetmap.org;");
    expect(csp).toContain("style-src 'self' 'unsafe-inline';");
    expect(csp).not.toMatch(/googleapis|gstatic|googleusercontent/);
  });
});
