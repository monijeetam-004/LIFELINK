/**
 * Donor Milestones, History & Certificates — adapted from the Stitch export
 * lifelink_2.6_2.8_donor_milestones_history_certificates/code.html.
 *
 * What's REAL here (see backend/app/api/routes/donors.py + certificates.py):
 *   - Donation history via listDonations().
 *   - Certificates via listCertificates() / issueCertificate() — download links use
 *     certificateDownloadUrl(), the same pattern as DonorDashboard.tsx and
 *     DonationCertificate.tsx (which this page links out to for the full document).
 *
 * What's FABRICATED in the original design and NOT reproduced: a hardcoded
 * "18 units / ~54 lives impacted" KPI strip, a "Community Pillar" badge tied to
 * consecutive-year tenure the backend never tracks, and per-row fake serials/
 * hospitals. The milestone badges below are instead computed purely from this
 * donor's real donation count (listDonations().length) — honest derived data,
 * not an invented achievement system with server state that doesn't exist.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Award, Lock } from "lucide-react";

import DonorSectionHeader from "../../components/DonorSectionHeader";
import LogDonationModal from "../../components/LogDonationModal";
import { toApiError } from "../../lib/api";
import {
  BLOOD_LABEL,
  certificateDownloadUrl,
  getDonorProfile,
  issueCertificate,
  listCertificates,
  listDonations,
} from "../../lib/domain";
import DonationVerificationBadge, { canRequestCertificate } from "../../components/DonationVerificationBadge";

const MILESTONES = [
  { threshold: 1, label: "First Donation", description: "Completed your first recorded donation." },
  { threshold: 5, label: "5 Donations", description: "Reached 5 recorded donations." },
  { threshold: 10, label: "10 Donations", description: "Reached 10 recorded donations." },
  { threshold: 25, label: "25 Donations", description: "Reached 25 recorded donations." },
];

export default function MilestonesHistory() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [showLogDonation, setShowLogDonation] = useState(false);
  const [certError, setCertError] = useState("");

  const profile = useQuery({ queryKey: ["donor", "me"], queryFn: getDonorProfile });
  const donations = useQuery({ queryKey: ["donor", "donations"], queryFn: listDonations });
  const certificates = useQuery({ queryKey: ["certificates"], queryFn: listCertificates });

  const certMut = useMutation({
    mutationFn: (donationId: string) => issueCertificate(donationId),
    onSuccess: (cert) => {
      qc.invalidateQueries({ queryKey: ["certificates"] });
      qc.invalidateQueries({ queryKey: ["notifications"] });
      setCertError("");
      navigate(`/certificate/${cert.id}`);
    },
    onError: (e) => setCertError(toApiError(e).detail),
  });

  const certByDonation = new Map((certificates.data ?? []).map((c) => [c.donation_id, c]));
  const donationCount = donations.data?.length ?? 0;
  const unitsGiven = (donations.data ?? []).reduce((n, d) => n + d.units, 0);

  return (
    <div className="min-h-screen bg-background">
      <DonorSectionHeader
        title="Milestones & History"
        subtitle="Your donation record, milestones, and certificates"
        icon={<span aria-hidden="true" className="material-symbols-outlined">history_edu</span>}
      />

      <main className="max-w-6xl mx-auto px-4 md:px-margin-desktop py-6 md:py-8 space-y-6 md:space-y-8">
        {(donations.isError || profile.isError) && (
          <div role="alert" className="bg-error-container text-on-error-container rounded-xl p-4 text-sm">
            Couldn't load your donation record: {toApiError(donations.error ?? profile.error).detail}{" "}
            <button
              onClick={() => {
                donations.refetch();
                profile.refetch();
              }}
              className="font-label-md hover:underline"
            >
              Retry
            </button>
          </div>
        )}

        {/* KPI strip — all real, derived from listDonations() */}
        <section className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="bg-surface rounded-xl border border-border-subtle shadow-sm p-5">
            <span className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
              Donations logged
            </span>
            <div className="font-headline-lg text-headline-lg text-primary mt-1">
              {donations.isLoading ? "—" : donationCount}
            </div>
          </div>
          <div className="bg-surface rounded-xl border border-border-subtle shadow-sm p-5">
            <span className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
              Units given
            </span>
            <div className="font-headline-lg text-headline-lg text-secondary mt-1">
              {donations.isLoading ? "—" : unitsGiven}
            </div>
          </div>
          <div className="bg-surface rounded-xl border border-border-subtle shadow-sm p-5">
            <span className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">
              Blood group on file
            </span>
            <div className="font-headline-lg text-headline-lg text-on-surface mt-1">
              {profile.data?.blood_type ? BLOOD_LABEL[profile.data.blood_type] : "Not set"}
            </div>
          </div>
        </section>

        {/* Milestones — computed from real donation count only */}
        <section className="space-y-3">
          <div>
            <h2 className="font-headline-sm text-headline-sm text-on-surface">Donation Milestones</h2>
            <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">
              Computed from your recorded donation count — not a separate tracked achievement.
            </p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {MILESTONES.map((m) => {
              const earned = donationCount >= m.threshold;
              const pct = Math.min(100, Math.round((donationCount / m.threshold) * 100));
              return (
                <div
                  key={m.threshold}
                  className={`rounded-xl p-5 shadow-sm flex flex-col justify-between gap-3 ${
                    earned ? "bg-surface border border-primary/30" : "bg-surface-container opacity-90"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <div
                      className={`w-12 h-12 rounded-xl flex items-center justify-center ${
                        earned ? "bg-primary/10 text-primary" : "bg-surface-container-highest text-on-surface-variant"
                      }`}
                    >
                      {earned ? <Award className="h-6 w-6" /> : <Lock className="h-6 w-6" />}
                    </div>
                    <span
                      className={`px-2 py-0.5 rounded-full font-label-sm text-label-sm font-bold uppercase tracking-wider ${
                        earned
                          ? "bg-primary/10 text-primary"
                          : "bg-surface-container-highest text-on-surface-variant"
                      }`}
                    >
                      {earned ? "Earned" : "Locked"}
                    </span>
                  </div>
                  <div>
                    <h3 className="font-title-md text-title-md text-on-surface font-bold">{m.label}</h3>
                    <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">{m.description}</p>
                  </div>
                  {!earned && (
                    <div className="space-y-1">
                      <div className="flex justify-between font-label-sm text-label-sm text-on-surface-variant">
                        <span>Progress</span>
                        <span>
                          {donationCount} / {m.threshold}
                        </span>
                      </div>
                      <div className="w-full h-2 rounded-full bg-surface-container-highest overflow-hidden">
                        <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        {/* Donation history — table on desktop, cards on mobile */}
        <section className="space-y-3">
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <h2 className="font-headline-sm text-headline-sm text-on-surface">Donation History</h2>
            <button
              onClick={() => setShowLogDonation(true)}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded-full bg-primary text-on-primary font-label-md text-label-md hover:opacity-90 transition-opacity"
            >
              <span aria-hidden="true" className="material-symbols-outlined text-[18px]">add</span>
              Log a donation
            </button>
          </div>
          {certError && (
            <p role="alert" className="text-sm text-error">
              Couldn't issue certificate: {certError}
            </p>
          )}
          {donations.isLoading && (
            <div className="bg-surface rounded-xl border border-border-subtle p-6 text-sm text-on-surface-variant">
              Loading…
            </div>
          )}
          {donations.data && donations.data.length === 0 && (
            <div className="bg-surface rounded-xl border border-border-subtle p-8 text-center text-sm text-on-surface-variant">
              No donations on record yet.{" "}
              <button onClick={() => setShowLogDonation(true)} className="text-secondary font-label-md hover:underline">
                Log your first one
              </button>
              .
            </div>
          )}
          {donations.data && donations.data.length > 0 && (
            <>
              {/* Desktop table */}
              <div className="hidden md:block bg-surface rounded-xl border border-border-subtle shadow-sm overflow-hidden">
                <div className="overflow-x-auto">
                  <table className="w-full text-left border-collapse">
                    <thead>
                      <tr className="bg-surface-container-low text-on-surface-variant font-label-sm text-label-sm uppercase tracking-wider">
                        <th className="py-3 px-4 font-semibold">Date</th>
                        <th className="py-3 px-4 font-semibold">Hospital</th>
                        <th className="py-3 px-4 font-semibold">Units</th>
                        <th className="py-3 px-4 font-semibold">Note</th>
                        <th className="py-3 px-4 font-semibold">Verification</th>
                        <th className="py-3 px-4 font-semibold text-right">Certificate</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-subtle text-on-surface">
                      {donations.data.map((d) => {
                        const cert = certByDonation.get(d.id);
                        return (
                          <tr key={d.id} className="hover:bg-surface-container-low transition-colors">
                            <td className="py-3 px-4 font-data-mono text-data-mono whitespace-nowrap">
                              {d.donated_on}
                            </td>
                            <td className="py-3 px-4">{d.hospital_name || "—"}</td>
                            <td className="py-3 px-4 font-data-mono text-data-mono">{d.units}</td>
                            <td className="py-3 px-4 text-on-surface-variant text-body-sm">{d.note || "—"}</td>
                            <td className="py-3 px-4">
                              <DonationVerificationBadge status={d.verification_status} />
                            </td>
                            <td className="py-3 px-4 text-right">
                              {cert ? (
                                <Link
                                  to={`/certificate/${cert.id}`}
                                  className="inline-flex items-center gap-1.5 text-secondary hover:underline font-label-sm text-label-sm"
                                >
                                  <span aria-hidden="true" className="material-symbols-outlined text-[16px]">workspace_premium</span>
                                  View certificate
                                </Link>
                              ) : !canRequestCertificate(d) ? (
                                <span className="text-on-surface-variant font-label-sm text-label-sm">
                                  Available after verification
                                </span>
                              ) : (
                                <button
                                  onClick={() => certMut.mutate(d.id)}
                                  disabled={certMut.isPending}
                                  className="inline-flex items-center gap-1.5 text-on-surface-variant hover:text-primary font-label-sm text-label-sm disabled:opacity-50"
                                >
                                  <span aria-hidden="true" className="material-symbols-outlined text-[16px]">verified</span>
                                  Get certificate
                                </button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Mobile card list */}
              <ul className="md:hidden space-y-3">
                {donations.data.map((d) => {
                  const cert = certByDonation.get(d.id);
                  return (
                    <li key={d.id} className="bg-surface rounded-xl border border-border-subtle shadow-sm p-4">
                      <div className="flex items-center justify-between">
                        <span className="font-label-md text-label-md text-on-surface">{d.donated_on}</span>
                        <span className="font-data-mono text-data-mono text-on-surface-variant">
                          {d.units} unit(s)
                        </span>
                      </div>
                      <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">
                        {d.hospital_name || "Hospital not specified"}
                      </p>
                      <div className="mt-1">
                        <DonationVerificationBadge status={d.verification_status} />
                      </div>
                      {d.note && <p className="font-body-sm text-body-sm text-on-surface-variant">{d.note}</p>}
                      <div className="mt-3 pt-3 border-t border-border-subtle">
                        {cert ? (
                          <Link
                            to={`/certificate/${cert.id}`}
                            className="inline-flex items-center gap-1.5 text-secondary hover:underline font-label-sm text-label-sm"
                          >
                            <span aria-hidden="true" className="material-symbols-outlined text-[16px]">workspace_premium</span>
                            View certificate
                          </Link>
                        ) : !canRequestCertificate(d) ? (
                          <span className="text-on-surface-variant font-label-sm text-label-sm">
                            Certificate available after a facility verifies this donation
                          </span>
                        ) : (
                          <button
                            onClick={() => certMut.mutate(d.id)}
                            disabled={certMut.isPending}
                            className="inline-flex items-center gap-1.5 text-on-surface-variant hover:text-primary font-label-sm text-label-sm disabled:opacity-50"
                          >
                            <span aria-hidden="true" className="material-symbols-outlined text-[16px]">verified</span>
                            Get certificate
                          </button>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </section>

        {/* Certificates */}
        <section className="space-y-3">
          <h2 className="font-headline-sm text-headline-sm text-on-surface">Certificates</h2>
          {certificates.isLoading && (
            <div className="bg-surface rounded-xl border border-border-subtle p-6 text-sm text-on-surface-variant">
              Loading…
            </div>
          )}
          {certificates.isError && (
            <div role="alert" className="bg-surface rounded-xl border border-border-subtle p-6 text-sm text-error flex items-center justify-between gap-3">
              <span>{toApiError(certificates.error).detail}</span>
              <button type="button" className="font-label-sm text-label-sm underline flex-shrink-0" onClick={() => certificates.refetch()}>
                Retry
              </button>
            </div>
          )}
          {certificates.data && certificates.data.length === 0 && (
            <div className="bg-surface rounded-xl border border-border-subtle p-8 text-center text-sm text-on-surface-variant">
              No certificates issued yet. Get one from a donation above once you've completed it.
            </div>
          )}
          {certificates.data && certificates.data.length > 0 && (
            <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              {certificates.data.map((c) => (
                <li key={c.id} className="bg-surface rounded-xl border border-border-subtle shadow-sm p-5 flex flex-col gap-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 text-primary min-w-0">
                      <span aria-hidden="true" className="material-symbols-outlined text-[20px]">workspace_premium</span>
                      <span className="font-data-mono text-data-mono text-on-surface truncate">{c.serial}</span>
                    </div>
                    <span
                      className={`px-2 py-0.5 rounded-full font-label-sm text-label-sm font-semibold flex-shrink-0 ${
                        c.revoked_at ? "bg-error/10 text-error" : "bg-success/15 text-success"
                      }`}
                    >
                      {c.revoked_at ? "Revoked" : "Valid"}
                    </span>
                  </div>
                  {c.revoked_at && (
                    <p className="font-body-sm text-body-sm text-error">
                      {c.revocation_reason ?? "This certificate has been revoked."}
                    </p>
                  )}
                  <p className="font-body-sm text-body-sm text-on-surface-variant">
                    {c.donated_on ?? "—"} · {c.units ?? "—"} unit(s)
                    {c.hospital_name ? ` · ${c.hospital_name}` : ""}
                  </p>
                  <div className="flex items-center gap-3 mt-1">
                    <Link
                      to={`/certificate/${c.id}`}
                      className="text-secondary hover:underline font-label-sm text-label-sm"
                    >
                      View
                    </Link>
                    <a
                      href={certificateDownloadUrl(c.id, c.download_token)}
                      className="text-on-surface-variant hover:text-primary font-label-sm text-label-sm"
                    >
                      Download PDF
                    </a>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>

      <LogDonationModal open={showLogDonation} onClose={() => setShowLogDonation(false)} />
    </div>
  );
}
