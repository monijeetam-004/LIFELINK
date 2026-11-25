/**
 * Family — Simplified Emergency Request & Status (per linked patient)
 * Adapted from: lifelink_1b.4_1b.5_family_simplified_emergency_request_status/code.html
 *
 * A focused, low-jargon view of one linked patient: file a new blood request or SOS for
 * them, and see the real-time status of everything for them -- what this family member
 * filed AND what the patient filed themselves from their own account (the backend scopes
 * /blood-requests and /emergencies to include both once the link is ACCEPTED). Reuses
 * BloodRequestModal / SosModal / RequestTrackDrawer / EmergencyTrackDrawer rather than
 * re-implementing tracking.
 */
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";

import BloodRequestModal from "../../components/BloodRequestModal";
import SosModal from "../../components/SosModal";
import RequestTrackDrawer from "../../components/RequestTrackDrawer";
import EmergencyTrackDrawer from "../../components/EmergencyTrackDrawer";
import NotificationsBell from "../../components/NotificationsBell";
import { toApiError } from "../../lib/api";
import {
  BLOOD_LABEL,
  listBloodRequests,
  listEmergencies,
  listFamilyLinks,
  type BloodRequestStatus,
  type EmergencyStatus,
} from "../../lib/domain";

const REQUEST_STATUS_STYLE: Record<BloodRequestStatus, string> = {
  PENDING: "bg-warning/15 text-warning",
  MATCHING: "bg-secondary/15 text-secondary",
  PARTIALLY_FULFILLED: "bg-secondary/15 text-secondary",
  FULFILLED: "bg-success/15 text-success",
  CANCELLED: "bg-error/10 text-error",
  EXPIRED: "bg-error/10 text-error",
};
const EMERGENCY_STATUS_STYLE: Record<EmergencyStatus, string> = {
  ACTIVE: "bg-error/10 text-error",
  ACKNOWLEDGED: "bg-secondary/15 text-secondary",
  MATCHING: "bg-secondary/15 text-secondary",
  ASSISTANCE_FOUND: "bg-success/15 text-success",
  RESOLVED: "bg-success/15 text-success",
  CANCELLED: "bg-error/10 text-error",
};

export default function FamilyPatientDetail() {
  const { patientId } = useParams<{ patientId: string }>();
  const [showNew, setShowNew] = useState(false);
  const [showSos, setShowSos] = useState(false);
  const [trackId, setTrackId] = useState<string | null>(null);
  const [emTrackId, setEmTrackId] = useState<string | null>(null);

  const links = useQuery({ queryKey: ["family", "links"], queryFn: listFamilyLinks });
  const requests = useQuery({ queryKey: ["blood-requests", "list"], queryFn: () => listBloodRequests() });
  const emergencies = useQuery({ queryKey: ["emergencies", "list"], queryFn: listEmergencies });

  const link = links.data?.find((l) => l.patient_user_id === patientId && l.status === "ACCEPTED");
  const myRequests = (requests.data ?? []).filter((r) => r.patient_user_id === patientId);
  const myEmergencies = (emergencies.data ?? []).filter((e) => e.patient_user_id === patientId);

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-30 h-16 bg-surface/90 backdrop-blur-md border-b border-border-subtle flex items-center justify-between px-margin-mobile md:px-margin-desktop">
        <div className="flex items-center gap-4 min-w-0">
          <Link
            to="/family"
            aria-label="Back to dashboard"
            className="p-2 rounded-full hover:bg-surface-container-low text-on-surface-variant flex-shrink-0"
          >
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div className="min-w-0">
            <h1 className="font-headline-md text-headline-md text-on-surface truncate">
              {link?.patient?.full_name ?? "Patient"}
            </h1>
            <p className="text-xs text-on-surface-variant">
              {link ? `${link.relationship} · ${link.patient?.email}` : "Loading…"}
            </p>
          </div>
        </div>
        <NotificationsBell buttonClassName="relative p-2 text-on-surface-variant hover:bg-surface-container rounded-full transition-all" />
      </header>

      <main className="max-w-3xl mx-auto p-margin-mobile md:p-margin-desktop space-y-8">
        {/* Phase 21 fix: this page's entire body used to be gated on `link`,
            derived only from `links.data` -- neither `links.isLoading` nor
            `links.isError` was ever rendered, so a failed fetch left the
            page permanently blank below the header (which itself only ever
            said "Loading…", never an error), indistinguishable from a
            hang. Now loading and error are both explicit, and only a
            confirmed absence of the link (data loaded, no match) shows the
            "no accepted link" message. */}
        {links.isLoading && (
          <p className="text-center text-on-surface-variant py-10">Loading…</p>
        )}
        {links.isError && (
          <div role="alert" className="rounded-xl bg-surface border border-border-subtle p-8 text-center">
            <p className="text-error text-sm mb-2">
              Couldn't load this patient. {toApiError(links.error).detail}
            </p>
            <button
              type="button"
              onClick={() => links.refetch()}
              className="text-secondary hover:underline text-sm font-label-md"
            >
              Retry
            </button>
          </div>
        )}
        {links.data && !link && (
          <div className="rounded-xl bg-surface border border-border-subtle p-8 text-center text-on-surface-variant">
            You don't have an accepted family link with this patient.{" "}
            <Link to="/family/patients" className="text-secondary hover:underline">
              Manage linked patients
            </Link>
          </div>
        )}

        {link && (
          <>
            {(requests.isError || emergencies.isError) && (
              <div role="alert" className="rounded-xl border border-error/30 bg-error/5 p-4 text-body-sm text-error">
                {requests.isError && <p>Couldn't load requests. {toApiError(requests.error).detail}</p>}
                {emergencies.isError && <p>Couldn't load emergencies. {toApiError(emergencies.error).detail}</p>}
              </div>
            )}
            <section className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <button
                onClick={() => setShowNew(true)}
                className="flex items-center gap-4 rounded-xl bg-surface border border-border-subtle p-6 hover:shadow-soft transition-all text-left"
              >
                <span className="w-12 h-12 rounded-lg bg-primary/10 text-primary flex items-center justify-center flex-shrink-0">
                  <span aria-hidden="true" className="material-symbols-outlined">add_circle</span>
                </span>
                <div>
                  <h3 className="font-headline-sm text-headline-sm text-on-surface">New blood request</h3>
                  <p className="text-sm text-on-surface-variant mt-0.5">File a routine or urgent request</p>
                </div>
              </button>
              <button
                onClick={() => setShowSos(true)}
                className="flex items-center gap-4 rounded-xl bg-error/5 border border-error/20 p-6 hover:shadow-soft transition-all text-left"
              >
                <span className="w-12 h-12 rounded-lg bg-error/10 text-error flex items-center justify-center flex-shrink-0">
                  <span aria-hidden="true" className="material-symbols-outlined">emergency</span>
                </span>
                <div>
                  <h3 className="font-headline-sm text-headline-sm text-error">Emergency SOS</h3>
                  <p className="text-sm text-on-surface-variant mt-0.5">Broadcast to nearby donors now</p>
                </div>
              </button>
            </section>

            {myEmergencies.length > 0 && (
              <section className="space-y-3">
                <h2 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs">
                  Emergencies
                </h2>
                <div className="bg-surface rounded-xl shadow-soft border border-border-subtle divide-y divide-border-subtle overflow-hidden">
                  {myEmergencies.map((e) => (
                    <button
                      key={e.id}
                      onClick={() => setEmTrackId(e.id)}
                      className="w-full flex items-center justify-between gap-4 px-5 py-4 text-left hover:bg-surface-container-lowest transition-colors"
                    >
                      <div className="min-w-0">
                        <p className="font-label-md text-label-md text-on-surface">
                          {e.blood_type ? BLOOD_LABEL[e.blood_type] : "Any type"}
                          {e.location_text ? ` · ${e.location_text}` : ""}
                        </p>
                        <p className="text-xs text-on-surface-variant">
                          Filed {new Date(e.created_at).toLocaleString()}
                          {e.filed_by_family ? " · Requested by family member" : ""}
                        </p>
                      </div>
                      <span className={`px-2.5 py-1 rounded-full text-xs font-bold flex-shrink-0 ${EMERGENCY_STATUS_STYLE[e.status]}`}>
                        {e.status.replace(/_/g, " ")}
                      </span>
                    </button>
                  ))}
                </div>
              </section>
            )}

            <section className="space-y-3">
              <h2 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs">
                Requests
              </h2>
              {myRequests.length === 0 ? (
                <p className="text-sm text-on-surface-variant">
                  No requests filed for {link.patient?.full_name ?? "this patient"} yet.
                </p>
              ) : (
                <div className="bg-surface rounded-xl shadow-soft border border-border-subtle divide-y divide-border-subtle overflow-hidden">
                  {myRequests.map((r) => (
                    <button
                      key={r.id}
                      onClick={() => setTrackId(r.id)}
                      className="w-full flex items-center justify-between gap-4 px-5 py-4 text-left hover:bg-surface-container-lowest transition-colors"
                    >
                      <div className="min-w-0">
                        <p className="font-label-md text-label-md text-on-surface">
                          {BLOOD_LABEL[r.blood_type]} ·{" "}
                          {r.fulfilled_units > 0 ? `${r.fulfilled_units}/${r.units_needed}` : r.units_needed} unit(s) ·{" "}
                          {r.urgency.toLowerCase()}
                        </p>
                        <p className="text-xs text-on-surface-variant">
                          {r.accepted_count} donor{r.accepted_count === 1 ? "" : "s"} accepted
                          {r.filed_by_family ? " · Requested by family member" : ""}
                        </p>
                      </div>
                      <span className={`px-2.5 py-1 rounded-full text-xs font-bold flex-shrink-0 ${REQUEST_STATUS_STYLE[r.status]}`}>
                        {r.status}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </section>

            <p className="text-xs text-on-surface-variant text-center">
              This shows every request and emergency for {link.patient?.full_name ?? "this patient"} --
              filed by you or by them directly -- for as long as your family link stays active.
            </p>
          </>
        )}
      </main>

      <BloodRequestModal open={showNew} onClose={() => setShowNew(false)} defaultPatientEmail={link?.patient?.email} />
      <SosModal open={showSos} onClose={() => setShowSos(false)} defaultPatientEmail={link?.patient?.email} />
      <RequestTrackDrawer requestId={trackId} onClose={() => setTrackId(null)} />
      <EmergencyTrackDrawer emergencyId={emTrackId} onClose={() => setEmTrackId(null)} mode="creator" />
    </div>
  );
}
