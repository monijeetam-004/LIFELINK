# LifeLink — Dependencies, Lockfiles and Advisories

## Python (backend)

| File | Role |
|---|---|
| `backend/requirements.txt` | Runtime ranges (what the app is compatible with). |
| `backend/requirements-dev.txt` | `-r requirements.txt` + test/lint tooling. |
| `backend/requirements.lock` | **Exact, hashed** runtime pins (53 packages) for CPython 3.12 / x86_64 Linux. Installed by the Docker image and CI. |
| `backend/requirements-dev.lock` | Exact, hashed runtime + dev pins (71 packages). Installed by every CI Python job. |

Install exactly what was tested:

```bash
pip install --require-hashes --no-deps -r backend/requirements.lock       # runtime / Docker
pip install --require-hashes --no-deps -r backend/requirements-dev.lock   # CI / development
```

`--require-hashes` makes pip refuse any artifact whose SHA-256 is not in the lock; `--no-deps` makes the lock the complete set (nothing resolved at install time).

### Updating the lock (documented procedure)

```bash
cd backend
# 1. change the range in requirements*.txt (or just refresh)
# 2. regenerate both locks with uv (https://docs.astral.sh/uv/), same target as CI/Docker:
uv pip compile requirements.txt     --python-version 3.12 --python-platform x86_64-unknown-linux-gnu \
    --generate-hashes --no-annotate -o requirements.lock \
    --custom-compile-command "uv pip compile requirements.txt --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --generate-hashes -o requirements.lock  (see docs/LIFELINK_DEPENDENCIES.md)"
uv pip compile requirements-dev.txt --python-version 3.12 --python-platform x86_64-unknown-linux-gnu \
    --generate-hashes --no-annotate -o requirements-dev.lock
# 3. verify: fresh venv, install with hashes, run the suite and the audit
python3.12 -m venv /tmp/v && /tmp/v/bin/pip install --require-hashes --no-deps -r requirements-dev.lock
/tmp/v/bin/pytest && pip-audit -r requirements.lock --require-hashes --disable-pip
```

To upgrade one package only: `uv pip compile … --upgrade-package <name>`. CI's *lock consistency* step re-resolves `requirements*.txt` against the committed lock (uv 0.12.23) and fails if they disagree, so a range change without a lock refresh cannot merge.

When the locks were first generated, uv was constrained to the versions already installed and tested in the development venv, so the lock pins what the suite had actually been run against rather than whatever was newest that day.

## JavaScript (frontend)

`frontend/package-lock.json` (npm, lockfileVersion 3, integrity hashes) — `npm ci` installs it exactly. `npm run audit:gate` (`scripts/audit-gate.mjs`) is the CI gate:

1. runtime dependencies (`npm audit --omit=dev`) must have **zero** advisories of any severity;
2. the full tree may have no high/critical advisory unless it is in `audit-allowlist.json` with a reason and a review-by date (expired entries fail).

### Current advisories (this round)

| Advisory | Package path | Status |
|---|---|---|
| GHSA-68fv-2mgg-jv7q (high) | `source-map-js` < 1.2.2 (via postcss, jsdom, coverage) | **Fixed** — `npm audit fix` → 1.2.2 (non-breaking). |
| GHSA-vfj7-8cjw-p6xm (high) | `braces` ≤ 3.0.3 via tailwindcss 3 → chokidar / micromatch / fast-glob | **Not fixable in Tailwind 3**: no patched `braces` exists (3.0.3 is the latest release and is in range). Build-time only; the only input is our own `tailwind.config.ts` `content` globs, never user data; absent from the shipped bundle. Allowlisted with review-by 2027-01-31. |
| GHSA-rj75-hqrm-r3gf (moderate) | `postcss-selector-parser` < 7.1.6 via tailwindcss 3 | Not fixable without Tailwind 4 (Tailwind 3 requires `^6`). Build-time only, parses our own CSS. Moderate → not gated. |

**Tailwind 4 evaluation:** Tailwind 4 drops chokidar/micromatch/fast-glob (Rust "oxide" scanner) and would clear both. It is a CSS-toolchain rewrite (CSS-first config, renamed utilities such as the shadow/rounded/ring scales, changed default border colour, `@tailwindcss/container-queries` folded in), touching every screen. It was **not** done in this round because its visual regressions could not be verified across all 24+ screens here; it is recorded as the way to clear these two dev-only advisories.

## Self-hosted assets (no runtime third-party asset hosts)

Fonts (Inter, Plus Jakarta Sans, Material Symbols Outlined) come from npm (`@fontsource-variable/inter`, `@fontsource-variable/plus-jakarta-sans` — OFL-1.1; `material-symbols` — Apache-2.0) and are bundled by Vite. The 25 former `lh3.googleusercontent.com` mockup images were replaced with local SVGs in `frontend/public/brand/` and `frontend/public/illustrations/`. The production CSP now allows only `'self'` for fonts and styles' external hosts, and only OpenStreetMap tiles as an external image host (`src/lib/selfHostedAssets.test.ts`). Offline, every screen renders except the map's tiles.
