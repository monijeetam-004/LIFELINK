/**
 * Public certificate verification. No auth — anyone with the non-guessable
 * code can confirm a certificate and see the safe fields the backend exposes.
 */
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { toApiError } from "../lib/api";
import { verifyCertificate } from "../lib/domain";

export default function VerifyCertificate() {
  const { code = "" } = useParams();
  const q = useQuery({
    queryKey: ["certificate-verify", code],
    queryFn: () => verifyCertificate(code),
    retry: false,
    enabled: !!code,
  });
  // Phase 21 fix: a 404 ("no certificate with this code") and a network/
  // server failure used to render the identical "Not verified / double-
  // check it was copied correctly" message -- misleading during an outage,
  // where the code may well be genuine. Only a confirmed 404 gets that
  // copy; anything else gets an honest "couldn't check" message with retry.
  const apiError = q.error ? toApiError(q.error) : null;
  const genuinelyNotFound = apiError?.status === 404;

  return (
    <div className="min-h-screen bg-background flex items-center justify-center px-4 py-16">
      <div className="w-full max-w-md bg-surface-container-lowest border border-outline-variant shadow-2xl rounded-xl overflow-hidden">
        <div className="bg-secondary text-on-secondary px-6 py-5 flex items-center gap-3">
          <span aria-hidden="true" className="material-symbols-outlined text-3xl">verified</span>
          <div>
            <p className="font-display-lg text-lg font-black tracking-tight">LifeLink</p>
            <p className="font-label-caps text-label-caps opacity-90">Certificate Verification</p>
          </div>
        </div>

        <div className="p-6">
          {q.isLoading && <p className="text-on-surface-variant">Checking certificate…</p>}

          {q.isError && genuinelyNotFound && (
            <div className="flex flex-col items-center text-center gap-2 py-6" role="alert">
              <span aria-hidden="true" className="material-symbols-outlined text-5xl text-error">cancel</span>
              <p className="font-headline-md text-headline-md text-error">Not verified</p>
              <p className="text-sm text-on-surface-variant">
                No certificate matches this code. Double-check it was copied correctly.
              </p>
            </div>
          )}
          {q.isError && !genuinelyNotFound && (
            <div className="flex flex-col items-center text-center gap-2 py-6" role="alert">
              <span aria-hidden="true" className="material-symbols-outlined text-5xl text-on-surface-variant">wifi_off</span>
              <p className="font-headline-md text-headline-md text-on-surface">Couldn't check this certificate</p>
              <p className="text-sm text-on-surface-variant">{apiError?.detail}</p>
              <button
                type="button"
                onClick={() => q.refetch()}
                className="mt-2 text-secondary text-sm font-label-md hover:underline"
              >
                Retry
              </button>
            </div>
          )}

          {q.data && q.data.valid && (
            <div className="flex items-center gap-2 text-success" role="status">
              <span aria-hidden="true" className="material-symbols-outlined" style={{ fontVariationSettings: "'FILL' 1" }}>
                check_circle
              </span>
              <span className="font-bold">Authentic certificate</span>
            </div>
          )}

          {/* Phase 31.1: a revoked certificate is a distinct outcome from
              both "genuinely not found" and "authentic" -- never say
              "invalid" here, and never silently fall back to the generic
              not-found copy above. Only `revoked_at`/`revocation_reason` as
              actually returned are used; nothing about who revoked it or
              when is fabricated beyond those two fields. */}
          {q.data && !q.data.valid && (
            <div
              role="alert"
              className="flex flex-col items-center text-center gap-2 py-2 mb-4 rounded-lg bg-error/10 px-4"
            >
              <span aria-hidden="true" className="material-symbols-outlined text-4xl text-error">gpp_bad</span>
              <p className="font-headline-sm text-headline-sm text-error">Certificate revoked</p>
              <p className="text-sm text-on-surface-variant">
                The certificate has been revoked
                {q.data.revocation_reason ? `: ${q.data.revocation_reason}` : "."}
              </p>
              {q.data.revoked_at && (
                <p className="text-xs text-on-surface-variant">
                  Revoked {new Date(q.data.revoked_at).toLocaleDateString()}
                </p>
              )}
            </div>
          )}

          {q.data && (
            <div className="flex flex-col gap-4">
              <dl className="divide-y divide-outline-variant text-sm">
                <div className="flex justify-between py-2">
                  <dt className="text-on-surface-variant">Donor</dt>
                  <dd className="font-semibold text-on-surface">{q.data.donor_name}</dd>
                </div>
                <div className="flex justify-between py-2">
                  <dt className="text-on-surface-variant">Serial</dt>
                  <dd className="font-mono text-on-surface">{q.data.serial}</dd>
                </div>
                <div className="flex justify-between py-2">
                  <dt className="text-on-surface-variant">Donation date</dt>
                  <dd className="text-on-surface">{q.data.donated_on}</dd>
                </div>
                <div className="flex justify-between py-2">
                  <dt className="text-on-surface-variant">Units</dt>
                  <dd className="text-on-surface">{q.data.units}</dd>
                </div>
                {q.data.hospital_name && (
                  <div className="flex justify-between py-2">
                    <dt className="text-on-surface-variant">Facility</dt>
                    <dd className="text-on-surface">{q.data.hospital_name}</dd>
                  </div>
                )}
                <div className="flex justify-between py-2">
                  <dt className="text-on-surface-variant">Issued</dt>
                  <dd className="text-on-surface">
                    {new Date(q.data.issued_at).toLocaleDateString()}
                  </dd>
                </div>
              </dl>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
