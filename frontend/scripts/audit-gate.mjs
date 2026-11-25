#!/usr/bin/env node
/**
 * Phase H -- npm dependency-audit gate (used by CI's dependency-audit job).
 *
 * Two rules, both hard failures:
 *
 *  1. Runtime dependencies (everything that ships in the browser bundle,
 *     `npm audit --omit=dev`) must have ZERO known advisories of ANY
 *     severity. This is stricter than the previous single
 *     `npm audit --audit-level=high` gate for shipped code.
 *
 *  2. The full tree (dev tooling included) must have no high/critical
 *     advisory except one listed in `audit-allowlist.json`. An allowlist
 *     entry is only honoured when
 *       - its advisory is not reachable from a runtime dependency (rule 1
 *         already guarantees this: any runtime advisory fails first), and
 *       - it names the advisory URL, the reason, and a review-by date; an
 *         entry past its review-by date fails the gate, so an exemption
 *         cannot silently become permanent.
 *
 * Nothing here downgrades a finding: an advisory that is not allowlisted
 * fails exactly as before.
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

function audit(extra) {
  let out;
  try {
    out = execFileSync("npm", ["audit", "--json", ...extra], { cwd: root, encoding: "utf8" });
  } catch (err) {
    // npm audit exits non-zero when it finds anything; the JSON is still on stdout.
    out = err.stdout;
    if (!out) throw err;
  }
  return JSON.parse(out);
}

function advisories(report) {
  const found = new Map();
  for (const [pkg, v] of Object.entries(report.vulnerabilities ?? {})) {
    for (const via of v.via) {
      if (typeof via === "object" && via.url) {
        found.set(via.url, { pkg, severity: via.severity, title: via.title, range: via.range });
      }
    }
  }
  return found;
}

const failures = [];

const runtime = advisories(audit(["--omit=dev"]));
for (const [url, a] of runtime) {
  failures.push(`RUNTIME ${a.severity} ${a.pkg}: ${a.title} (${url})`);
}

const allowlist = JSON.parse(readFileSync(join(root, "audit-allowlist.json"), "utf8")).advisories;
const today = new Date().toISOString().slice(0, 10);
const allowed = new Map();
for (const entry of allowlist) {
  if (!entry.url || !entry.reason || !entry.review_by) {
    failures.push(`allowlist entry incomplete: ${JSON.stringify(entry)}`);
    continue;
  }
  if (entry.review_by < today) {
    failures.push(`allowlist entry expired on ${entry.review_by}: ${entry.url} -- re-review it`);
    continue;
  }
  allowed.set(entry.url, entry);
}

const full = advisories(audit([]));
let exempted = 0;
for (const [url, a] of full) {
  if (a.severity !== "high" && a.severity !== "critical") continue;
  if (allowed.has(url) && !runtime.has(url)) {
    exempted += 1;
    console.log(`allowlisted (dev-only) ${a.severity} ${a.pkg}: ${a.title} -- ${allowed.get(url).reason}`);
    continue;
  }
  failures.push(`${a.severity} ${a.pkg}: ${a.title} (${url})`);
}

if (failures.length) {
  console.error("npm audit gate FAILED:");
  for (const f of failures) console.error(`  - ${f}`);
  process.exit(1);
}
console.log(
  `npm audit gate passed: 0 runtime advisories; ${full.size} advisories in the full tree, ` +
    `${exempted} high/critical exempted by audit-allowlist.json (dev-only).`,
);
