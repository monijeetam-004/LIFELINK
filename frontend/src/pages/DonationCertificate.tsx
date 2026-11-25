/**
 * Digital Donation Certificate.
 *
 * Phase 6 bound this page to real certificates (PDF download, public
 * verification link); Phase 31.1 added honest revocation display.
 *
 * Redesign (after the hardening round): the original Stitch export used
 * spacing/font tokens (`px-lg`, `gap-md`, `font-cert-name`, ...) that this
 * project's Tailwind config never defined, so the page rendered with
 * collapsed spacing, overlapping text and fallback fonts. It now uses only
 * standard Tailwind utilities + the project's colour tokens. The old
 * decorative "QR code" was an icon, not a scannable code, so it is replaced
 * by the real verification code and link. Data, links and the revocation
 * contract are unchanged.
 */
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { toApiError } from "../lib/api";
import { certificateDownloadUrl, listCertificates } from "../lib/domain";

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export default function DonationCertificate() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["certificates"], queryFn: listCertificates });

  const certs = q.data ?? [];
  const cert = id ? certs.find((c) => c.id === id) : certs[0];

  const issueDate = formatDate(cert?.created_at);
  const donatedOn = formatDate(cert?.donated_on);
  const verifyHref = cert?.verify_path ?? "#";
  const verifyLabel = cert ? `${window.location.host}${cert.verify_path ?? ""}` : "";
  const pdfHref = cert ? certificateDownloadUrl(cert.id, cert.download_token) : "#";
  const revoked = Boolean(cert?.revoked_at);
  const disabled = cert ? "" : "pointer-events-none opacity-40";

  return (
    <div className="min-h-screen bg-slate-100 font-body-md">
      {/* ---------------------------------------------------------------- */}
      {/* Top bar                                                          */}
      {/* ---------------------------------------------------------------- */}
      <header className="sticky top-0 z-40 bg-white/90 backdrop-blur border-b border-slate-200 no-print">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0">
            <Link
              to="/donor/milestones"
              className="inline-flex items-center justify-center w-9 h-9 rounded-full text-slate-600 hover:bg-slate-100"
              aria-label="Back to my donations"
            >
              <span aria-hidden="true" className="material-symbols-outlined">arrow_back</span>
            </Link>
            <img alt="" className="h-8 w-8" src="/brand/lifelink-logo.svg" />
            <div className="min-w-0">
              <p className="font-headline-xl text-base font-bold text-slate-900 leading-tight">LifeLink</p>
              <p className="text-xs text-slate-500 leading-tight truncate">Donation certificate</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <a
              href={verifyHref}
              target="_blank"
              rel="noreferrer"
              className={`hidden sm:inline-flex items-center gap-2 h-10 px-4 rounded-lg border border-slate-300 bg-white text-sm font-semibold text-slate-700 hover:bg-slate-50 ${disabled}`}
            >
              <span aria-hidden="true" className="material-symbols-outlined text-[20px]">verified</span>
              Verify online
            </a>
            <a
              href={pdfHref}
              aria-label="Download PDF"
              className={`inline-flex items-center gap-2 h-10 px-4 whitespace-nowrap rounded-lg bg-primary text-white text-sm font-semibold shadow-sm hover:opacity-90 ${disabled}`}
            >
              <span aria-hidden="true" className="material-symbols-outlined text-[20px]">download</span>
              <span className="sm:hidden">PDF</span>
              <span className="hidden sm:inline">Download PDF</span>
            </a>
          </div>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-4 sm:px-6 py-8">
        {/* -------------------------------------------------------------- */}
        {/* Document meta + status                                         */}
        {/* -------------------------------------------------------------- */}
        <div className="max-w-[960px] mx-auto mb-5 flex flex-wrap items-center justify-between gap-3 no-print">
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm text-slate-600">
            <span className="font-medium text-slate-800">Document ID: {cert?.serial ?? "—"}</span>
            {cert && <span>Issued {issueDate}</span>}
          </div>
          <div
            className={`inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-sm font-semibold ${
              !cert
                ? "bg-slate-200 text-slate-600"
                : revoked
                  ? "bg-red-100 text-red-800"
                  : "bg-emerald-100 text-emerald-800"
            }`}
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[18px]" style={{ fontVariationSettings: "'FILL' 1" }}>
              {revoked ? "gpp_bad" : "check_circle"}
            </span>
            <span className="sr-only">Document status: </span>
            <span>{cert ? (revoked ? "Revoked" : "Verified Digital Copy") : "No certificate"}</span>
          </div>
        </div>

        {/* Phase 31.1: only `revoked_at`/`revocation_reason` as actually
            returned -- never a fabricated "revoked_by" or status label. The
            certificate itself stays visible as historical evidence. */}
        {cert?.revoked_at && (
          <div
            role="alert"
            className="max-w-[960px] mx-auto mb-5 rounded-xl border border-red-200 bg-red-50 text-red-800 px-4 py-3 text-sm flex items-start gap-2 no-print"
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[20px] flex-shrink-0">gpp_bad</span>
            <span>
              This certificate has been revoked
              {cert.revocation_reason ? `: ${cert.revocation_reason}` : "."}{" "}
              ({new Date(cert.revoked_at).toLocaleDateString()})
            </span>
          </div>
        )}

        {q.isLoading && (
          <p className="max-w-[960px] mx-auto text-slate-500 text-center py-16">Loading your certificates…</p>
        )}
        {/* Phase 21: a fetch failure is shown as a failure, never as the
            "No certificate yet" empty state. */}
        {q.isError && (
          <div className="max-w-[960px] mx-auto text-center py-16 bg-white rounded-2xl border border-slate-200" role="alert">
            <p className="text-xl font-semibold text-red-700 mb-2">Couldn't load your certificates</p>
            <p className="text-slate-600 text-sm">{toApiError(q.error).detail}</p>
            <button
              type="button"
              onClick={() => q.refetch()}
              className="mt-4 text-secondary text-sm font-semibold hover:underline"
            >
              Retry
            </button>
          </div>
        )}
        {!q.isLoading && !q.isError && !cert && (
          <div className="max-w-[960px] mx-auto text-center py-16 bg-white rounded-2xl border border-slate-200">
            <p className="text-xl font-semibold text-slate-900 mb-2">No certificate yet</p>
            <p className="text-slate-600 text-sm">
              Certificates are issued from a completed donation. Open your{" "}
              <Link to="/donor" className="text-secondary hover:underline">Donor dashboard</Link>{" "}
              and choose <span className="font-semibold">Get certificate</span> on a donation.
            </p>
          </div>
        )}

        {cert && (
          <>
            {certs.length > 1 && (
              <div className="max-w-[960px] mx-auto mb-5 flex flex-wrap gap-2 no-print">
                {certs.map((c) => (
                  <Link
                    key={c.id}
                    to={`/certificate/${c.id}`}
                    className={`text-xs font-semibold px-3 py-1.5 rounded-full border ${
                      c.id === cert.id
                        ? "bg-slate-900 text-white border-slate-900"
                        : "bg-white border-slate-300 text-slate-600 hover:border-slate-400"
                    }`}
                  >
                    {c.serial}
                  </Link>
                ))}
              </div>
            )}

            {/* ---------------------------------------------------------- */}
            {/* The certificate                                            */}
            {/* ---------------------------------------------------------- */}
            <article
              aria-label="Donation certificate"
              className="cert-canvas relative max-w-[960px] mx-auto bg-white rounded-sm shadow-xl ring-1 ring-slate-200 md:min-h-[680px]"
            >
              {/* double frame */}
              <div className="absolute inset-3 sm:inset-4 border-2 border-primary/80 pointer-events-none" />
              <div className="absolute inset-5 sm:inset-6 border border-primary/30 pointer-events-none" />
              {/* corner accents */}
              <div className="absolute top-3 left-3 sm:top-4 sm:left-4 w-14 h-14 border-t-4 border-l-4 border-primary pointer-events-none" />
              <div className="absolute top-3 right-3 sm:top-4 sm:right-4 w-14 h-14 border-t-4 border-r-4 border-primary pointer-events-none" />
              <div className="absolute bottom-3 left-3 sm:bottom-4 sm:left-4 w-14 h-14 border-b-4 border-l-4 border-primary pointer-events-none" />
              <div className="absolute bottom-3 right-3 sm:bottom-4 sm:right-4 w-14 h-14 border-b-4 border-r-4 border-primary pointer-events-none" />

              <div className="relative h-full flex flex-col items-center text-center px-8 sm:px-16 py-10 sm:py-12">
                {/* heading */}
                <img alt="LifeLink official seal" className="h-20 w-20 sm:h-24 sm:w-24" src="/brand/lifelink-seal.svg" />
                <p className="mt-3 text-xs sm:text-sm font-semibold tracking-[0.35em] text-primary">LIFELINK</p>
                <h1 className="mt-2 font-headline-xl text-2xl sm:text-4xl font-bold tracking-tight text-slate-900">
                  Certificate of Life Saving
                </h1>
                <div className="mt-3 flex items-center gap-3" aria-hidden="true">
                  <span className="h-px w-10 bg-primary/40" />
                  <span className="h-1.5 w-1.5 rotate-45 bg-primary" />
                  <span className="h-px w-10 bg-primary/40" />
                </div>

                {/* recipient */}
                <p className="mt-5 text-sm sm:text-base italic text-slate-500">This certificate is proudly presented to</p>
                <h2 className="mt-2 font-headline-xl text-3xl sm:text-5xl font-bold text-primary leading-tight break-words">
                  {cert.donor_name}
                </h2>
                <div className="mt-3 h-px w-64 max-w-full bg-slate-300" />

                <p className="mt-4 max-w-2xl text-sm sm:text-base leading-relaxed text-slate-700">
                  In grateful recognition of a life-saving blood donation
                  {cert.hospital_name ? ` recorded at ${cert.hospital_name}` : " recorded on the LifeLink network"}.
                  Your generosity helps patients receive blood when it matters most.
                </p>

                {/* facts */}
                <dl className="mt-6 grid grid-cols-3 gap-3 sm:gap-6 w-full max-w-xl">
                  <div className="rounded-lg bg-slate-50 border border-slate-200 px-3 py-2">
                    <dt className="text-[10px] sm:text-xs uppercase tracking-wider text-slate-500">Donated on</dt>
                    <dd className="text-xs sm:text-sm font-semibold text-slate-900">{donatedOn}</dd>
                  </div>
                  <div className="rounded-lg bg-slate-50 border border-slate-200 px-3 py-2">
                    <dt className="text-[10px] sm:text-xs uppercase tracking-wider text-slate-500">Units</dt>
                    <dd className="text-xs sm:text-sm font-semibold text-slate-900">{cert.units ?? "—"}</dd>
                  </div>
                  <div className="rounded-lg bg-slate-50 border border-slate-200 px-3 py-2">
                    <dt className="text-[10px] sm:text-xs uppercase tracking-wider text-slate-500">Issued</dt>
                    <dd className="text-xs sm:text-sm font-semibold text-slate-900">{issueDate}</dd>
                  </div>
                </dl>

                {/* signatures + medallion */}
                <div className="mt-auto pt-8 w-full grid grid-cols-3 items-end gap-4">
                  <div className="flex flex-col items-center">
                    <span className="w-full max-w-[200px] border-b border-slate-400 mb-2" />
                    <span className="text-[10px] sm:text-xs uppercase tracking-wider text-slate-500">Recorded at</span>
                    <span className="text-xs sm:text-sm font-semibold text-slate-900">
                      {cert.hospital_name ?? "LifeLink Network"}
                    </span>
                  </div>
                  <div className="flex justify-center">
                    <div className="seal-emboss w-20 h-20 sm:w-24 sm:h-24 rounded-full border-4 border-white flex flex-col items-center justify-center text-white shadow-md">
                      <span aria-hidden="true" className="material-symbols-outlined text-3xl" style={{ fontVariationSettings: "'FILL' 1" }}>
                        workspace_premium
                      </span>
                      <span className="text-[9px] sm:text-[10px] font-bold uppercase leading-tight">Life saver</span>
                    </div>
                  </div>
                  <div className="flex flex-col items-center">
                    <span className="w-full max-w-[200px] border-b border-slate-400 mb-2" />
                    <span className="text-[10px] sm:text-xs uppercase tracking-wider text-slate-500">Issued by</span>
                    <span className="text-xs sm:text-sm font-semibold text-slate-900">LifeLink Platform</span>
                  </div>
                </div>

                {/* verification */}
                <p className="mt-6 mb-2 text-[10px] sm:text-xs text-slate-500 break-all">
                  Certificate {cert.serial} · Verify at{" "}
                  <a href={verifyHref} target="_blank" rel="noreferrer" className="text-secondary hover:underline">
                    {verifyLabel}
                  </a>
                </p>
              </div>
            </article>

            <p className="max-w-[960px] mx-auto mt-4 text-center text-xs text-slate-600 no-print">
              Anyone can confirm this certificate with its verification link. The PDF download contains the same details.
            </p>
          </>
        )}
      </main>
    </div>
  );
}
