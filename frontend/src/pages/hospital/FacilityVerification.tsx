/**
 * Hospital — Facility Verification (Phase 14)
 *
 * The backend workflow (POST /verifications, GET /verifications/me, and the
 * admin review queue at /admin/verifications) already existed and already
 * supports VerificationKind.HOSPITAL_FACILITY end-to-end — this page was the
 * missing piece: donors had IdentityVerification.tsx, hospitals had no
 * equivalent screen at all. Deliberately mirrors that page's real-backend
 * pattern (no mock state, no fake "Verified" badge): "Verified" only shows
 * once HospitalProfile.verified is true, which only ever happens when an
 * admin approves a HOSPITAL_FACILITY request server-side.
 */
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, X } from "lucide-react";

import HospitalSubNav from "./HospitalSubNav";
import { toApiError } from "../../lib/api";
import { getHospitalProfile, listMyVerifications, submitVerification } from "../../lib/domain";

// Mirrors the real server-side limits (settings.verification_max_file_bytes /
// verification_allowed_content_type_list) — the server is what actually
// enforces this; these are just an early, friendlier client-side check.
const MAX_FILE_BYTES = 10 * 1024 * 1024; // 10 MB
const ACCEPTED_TYPES = ["application/pdf", "image/jpeg", "image/png"];

interface PickedFile {
  file: File;
  error: string | null;
}

export default function FacilityVerification() {
  const qc = useQueryClient();
  const profile = useQuery({ queryKey: ["hospital", "me"], queryFn: getHospitalProfile });
  const verifications = useQuery({ queryKey: ["verifications", "me"], queryFn: listMyVerifications });

  const [files, setFiles] = useState<PickedFile[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  const submit = useMutation({
    mutationFn: () => submitVerification("HOSPITAL_FACILITY", files.map((f) => f.file)),
    onSuccess: () => {
      setFiles([]);
      qc.invalidateQueries({ queryKey: ["verifications", "me"] });
      qc.invalidateQueries({ queryKey: ["hospital", "me"] });
    },
  });

  function onPick(list: FileList | null) {
    if (!list) return;
    const next: PickedFile[] = Array.from(list).map((file) => {
      let error: string | null = null;
      if (!ACCEPTED_TYPES.includes(file.type)) error = "Unsupported file type — use PDF, JPG, or PNG.";
      else if (file.size > MAX_FILE_BYTES) error = "File is larger than 10 MB.";
      return { file, error };
    });
    setFiles((prev) => [...prev, ...next]);
    submit.reset();
    if (inputRef.current) inputRef.current.value = "";
  }

  function removeFile(idx: number) {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  }

  const hasBlockingError = files.some((f) => f.error);
  const canSubmit = files.length > 0 && !hasBlockingError && !submit.isPending;

  const myFacilityRequests = (verifications.data ?? []).filter((v) => v.kind === "HOSPITAL_FACILITY");
  const latest = [...myFacilityRequests].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )[0];

  const isVerified = profile.data?.verified === true;
  const status = isVerified ? "APPROVED" : latest?.status ?? null;

  const banner = (() => {
    if (isVerified) {
      return {
        icon: "verified",
        iconClass: "text-success",
        title: "Verified facility",
        body: "This facility has been verified. The verified badge is visible to donors and patients, and lets this facility be targeted directly during an emergency SOS.",
      };
    }
    // Phase 21 fix: a failed `verifications` fetch used to leave `latest`
    // undefined and fall through to the same "Not submitted" default as a
    // facility that genuinely never submitted -- misleading. Only show
    // "Not submitted" once we actually know that's true.
    if (verifications.isError) {
      return {
        icon: "error",
        iconClass: "text-error",
        title: "Couldn't load your verification status",
        body: toApiError(verifications.error).detail,
      };
    }
    if (verifications.isLoading) {
      return {
        icon: "hourglass_top",
        iconClass: "text-on-surface-variant",
        title: "Checking your verification status…",
        body: "",
      };
    }
    if (status === "PENDING") {
      return {
        icon: "hourglass_top",
        iconClass: "text-on-surface-variant",
        title: "Submitted — awaiting review",
        body: `Version ${latest!.version}, submitted ${new Date(latest!.created_at).toLocaleString()}. An admin will review your facility documents.`,
      };
    }
    if (status === "REJECTED") {
      return {
        icon: "error",
        iconClass: "text-error",
        title: "Rejected",
        body: latest?.review_note
          ? `Reason: ${latest.review_note} — you may submit new documents below.`
          : "Your submission was rejected. You may submit new documents below.",
      };
    }
    return {
      icon: "cloud_off",
      iconClass: "text-on-surface-variant",
      title: "Not submitted",
      body: "Upload your facility registration/license documents below to start verification.",
    };
  })();

  const stages = [
    { n: 1, title: "Prepare documents", body: "Gather your facility's registration/license and any supporting records.", done: true },
    { n: 2, title: "Submit for review", body: "Send the documents to LifeLink for review.", done: !!latest || isVerified },
    { n: 3, title: "Admin review", body: "An admin checks the submitted documents against this facility's profile.", done: status === "PENDING" || status === "APPROVED" || status === "REJECTED" || isVerified },
    { n: 4, title: "Verification decision", body: "You're notified of the outcome and the verified badge is applied.", done: isVerified || status === "REJECTED" },
  ];

  return (
    <div className="min-h-screen bg-background">
      <HospitalSubNav title="Facility Verification" subtitle="Verify this facility to build trust with donors and patients" />

      <main className="max-w-4xl mx-auto px-4 md:px-margin-desktop py-6 md:py-8 space-y-6 md:space-y-8">
        <section className="bg-surface rounded-xl border border-border-subtle shadow-sm p-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 rounded-lg bg-surface-container-high flex items-center justify-center text-on-surface-variant flex-shrink-0">
              <span aria-hidden="true" className={`material-symbols-outlined text-[24px] ${banner.iconClass}`}>{banner.icon}</span>
            </div>
            <div>
              <p className="font-title-md text-title-md text-on-surface font-bold">{banner.title}</p>
              <p className="font-body-sm text-body-sm text-on-surface-variant">{banner.body}</p>
            </div>
          </div>
          {verifications.isError && (
            <button
              type="button"
              onClick={() => verifications.refetch()}
              className="text-secondary text-sm font-label-md hover:underline flex-shrink-0"
            >
              Retry
            </button>
          )}
        </section>

        <section className="bg-surface rounded-xl border border-border-subtle shadow-sm p-6 space-y-3">
          <h2 className="font-headline-sm text-headline-sm text-on-surface">Facility information on file</h2>
          {profile.isError && (
            <p className="text-sm text-error">{toApiError(profile.error).detail}</p>
          )}
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <dt className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">Facility name</dt>
              <dd className="font-body-md text-body-md text-on-surface">
                {profile.isLoading ? "Loading…" : profile.data?.name || "Not set"}
              </dd>
            </div>
            <div>
              <dt className="font-label-sm text-label-sm uppercase tracking-wider text-on-surface-variant">City</dt>
              <dd className="font-body-md text-body-md text-on-surface">
                {profile.isLoading ? "Loading…" : profile.data?.city || "Not set"}
              </dd>
            </div>
          </dl>
        </section>

        <section className="bg-surface rounded-xl border border-border-subtle shadow-sm p-6 space-y-4">
          <h2 className="font-headline-sm text-headline-sm text-on-surface">Verification Stages</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {stages.map((s) => (
              <div
                key={s.n}
                className={`rounded-lg p-4 flex flex-col gap-2 ${
                  // Phase 27: this card already distinguishes "not yet
                  // done" from "done" via background shade, icon
                  // (hourglass_empty vs check_circle) and badge color --
                  // the extra `opacity-70` here was redundant emphasis
                  // that dropped this card's own text/icon color below
                  // WCAG AA contrast (measured ~3.5:1, real axe-core
                  // finding). Removed; the state is still fully legible.
                  s.done ? "bg-surface-container-low" : "bg-surface-container"
                }`}
              >
                <div className="flex items-center justify-between">
                  <span
                    className={`w-7 h-7 rounded-full flex items-center justify-center font-label-md text-label-md font-bold ${
                      s.done
                        ? "bg-secondary text-on-secondary"
                        : "bg-surface-container-highest text-on-surface-variant"
                    }`}
                  >
                    {String(s.n).padStart(2, "0")}
                  </span>
                  <span aria-hidden="true" className="material-symbols-outlined text-[18px] text-on-surface-variant">
                    {s.done ? "check_circle" : "hourglass_empty"}
                  </span>
                </div>
                <div className="font-title-md text-title-md text-on-surface font-semibold">{s.title}</div>
                <p className="font-body-sm text-body-sm text-on-surface-variant">{s.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="bg-surface rounded-xl border border-border-subtle shadow-sm p-6 space-y-4">
          <div>
            <h2 className="font-headline-sm text-headline-sm text-on-surface">Upload facility documents</h2>
            <p className="font-body-sm text-body-sm text-on-surface-variant mt-1">
              PDF, JPG, or PNG, up to 10 MB each — facility registration, license, or accreditation.
            </p>
          </div>

          <label
            htmlFor="facility-doc-input"
            className="flex flex-col items-center justify-center gap-2 border-2 border-dashed border-outline-variant rounded-xl p-8 text-center cursor-pointer hover:bg-surface-container-low transition-colors"
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[32px] text-on-surface-variant">upload_file</span>
            <span className="font-label-md text-label-md text-on-surface">Click to choose files</span>
            <span className="font-body-sm text-body-sm text-on-surface-variant">or drag and drop</span>
          </label>
          <input
            id="facility-doc-input"
            ref={inputRef}
            type="file"
            multiple
            accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"
            className="sr-only"
            onChange={(e) => onPick(e.target.files)}
          />

          {files.length > 0 && (
            <ul className="space-y-2">
              {files.map((f, idx) => (
                <li
                  key={`${f.file.name}-${idx}`}
                  className={`flex items-center justify-between gap-3 p-3 rounded-lg ${
                    f.error ? "bg-error-container text-on-error-container" : "bg-surface-container-low"
                  }`}
                >
                  <div className="min-w-0 flex-1">
                    <p className="font-data-mono text-data-mono truncate">{f.file.name}</p>
                    <p className="font-label-sm text-label-sm opacity-80">
                      {f.error ?? `${(f.file.size / (1024 * 1024)).toFixed(1)} MB`}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeFile(idx)}
                    aria-label={`Remove ${f.file.name}`}
                    className="p-1.5 rounded-full hover:bg-black/10 flex-shrink-0"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </li>
              ))}
            </ul>
          )}

          <button
            type="button"
            disabled={!canSubmit}
            onClick={() => submit.mutate()}
            className="w-full sm:w-auto bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md px-6 py-3 rounded-xl transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {submit.isPending ? (
              <Loader2 className="h-5 w-5 animate-spin" />
            ) : (
              <span aria-hidden="true" className="material-symbols-outlined text-[20px]">upload</span>
            )}
            Submit for review
          </button>

          {submit.isError && (
            // QA a11y fix: announced to screen readers (was a silent div).
            <div role="alert" className="p-4 rounded-lg bg-error-container text-on-error-container text-body-sm flex items-start gap-2">
              <span className="material-symbols-outlined text-[18px] mt-0.5" aria-hidden="true">error</span>
              <span>{toApiError(submit.error).detail}</span>
            </div>
          )}

          {submit.isSuccess && (
            <div className="p-4 rounded-lg bg-success/15 text-success text-body-sm flex items-start gap-2">
              <span aria-hidden="true" className="material-symbols-outlined text-[18px] mt-0.5">check_circle</span>
              <span>Your documents were submitted for review. We'll notify you once a decision is made.</span>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
