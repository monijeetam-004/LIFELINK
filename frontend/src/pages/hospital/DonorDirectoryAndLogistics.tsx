/**
 * Anonymized donor directory + live donor/logistics map for one blood
 * request.
 *
 * Adapted from the Stitch export
 * `lifelink_3.2_3.10_anonymized_donor_directory_live_map_logistics/code.html`.
 * That mock's donor cards show fabricated PII (hemoglobin readings, "prior
 * donations", rest-window countdowns, pod/route assignments) that has no
 * backend source. This screen renders only the real fields
 * `GET /blood-requests/{id}/match-candidates` returns to a hospital caller:
 * `donor_ref` (8-char id prefix — never a name), `blood_type`,
 * `is_available`, `has_accepted`, `distance_km` (null unless the donor
 * opted into `share_location`), `last_donation_date`, `city`. Per
 * `backend/app/api/routes/matching.py`, `donor_name` is always `null` for a
 * HOSPITAL caller — this screen never reads that field. The live map reuses
 * `<DonorMap />`, which is already wired to the equally anonymized
 * `/map/data` endpoint.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Loader2 } from "lucide-react";

import HospitalSubNav from "./HospitalSubNav";
import DonorMap from "../../components/DonorMap";
import { toApiError } from "../../lib/api";
import {
  BLOOD_LABEL,
  getMatchCandidates,
  listHospitalRequests,
  type HospitalRequestItem,
  type MatchCandidate,
} from "../../lib/domain";

type AvailFilter = "ALL" | "AVAILABLE" | "ACCEPTED";

export default function DonorDirectoryAndLogistics() {
  const [params, setParams] = useSearchParams();
  const requestId = params.get("request");

  return (
    <div className="min-h-screen bg-surface">
      <HospitalSubNav title="Donor Directory & Live Map" subtitle="Anonymized — no donor identity is ever shown" />
      <main className="px-4 sm:px-6 lg:px-10 py-6 max-w-container-max mx-auto flex flex-col gap-6">
        <PrivacyBanner />
        {requestId ? (
          <DirectoryForRequest
            requestId={requestId}
            onChangeRequest={() => setParams({}, { replace: true })}
          />
        ) : (
          <RequestPicker onPick={(id) => setParams({ request: id })} />
        )}
      </main>
    </div>
  );
}

function PrivacyBanner() {
  return (
    <div className="w-full bg-surface-container-high rounded-xl p-4 flex items-start gap-3 shadow-sm">
      <div className="w-10 h-10 rounded-full bg-primary-container text-on-primary flex items-center justify-center shrink-0">
        <span aria-hidden="true" className="material-symbols-outlined text-[20px]">shield_with_heart</span>
      </div>
      <p className="font-body-sm text-body-sm text-on-surface-variant">
        <span className="font-bold text-on-surface">Donor identity is never shown to hospitals.</span>{" "}
        Every donor below is a coded <code className="font-data-mono">donor_ref</code>. Names, phone
        numbers, emails and photos are withheld by the backend — this screen never receives them, even
        for accepted donors.
      </p>
    </div>
  );
}

function RequestPicker({ onPick }: { onPick: (id: string) => void }) {
  const q = useQuery({
    queryKey: ["hospital", "requests", "picker"],
    queryFn: () => listHospitalRequests(false),
  });

  return (
    <section className="flex flex-col gap-4">
      <div>
        <h1 className="font-headline-lg text-headline-lg text-on-surface">Choose a request</h1>
        <p className="font-body-md text-body-md text-on-surface-variant mt-1">
          Pick one of your open incoming requests to see its compatible, anonymized donors and the live
          map.
        </p>
      </div>

      {q.isLoading && (
        <div className="flex items-center gap-2 text-on-surface-variant py-8 justify-center">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading requests…
        </div>
      )}
      {q.isError && (
        <div className="text-error text-body-sm flex items-center gap-3" role="alert">
          <span>{toApiError(q.error).detail}</span>
          <button type="button" className="underline flex-shrink-0" onClick={() => q.refetch()}>
            Retry
          </button>
        </div>
      )}
      {q.data && q.data.length === 0 && (
        <div className="rounded-xl border border-border-subtle bg-surface-container-lowest p-8 text-center text-on-surface-variant">
          No open incoming requests right now.{" "}
          <Link to="/hospital" className="text-secondary underline">
            Back to Hospital Operations
          </Link>
        </div>
      )}
      {q.data && q.data.length > 0 && (
        <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {q.data.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => onPick(r.id)}
                className="w-full text-left bg-surface-container-lowest p-4 rounded-xl border border-border-subtle shadow-sm hover:shadow-md transition-shadow"
              >
                <div className="flex justify-between mb-2">
                  <span className="text-[10px] font-bold bg-primary-fixed text-on-primary-fixed-variant px-2 py-0.5 rounded uppercase">
                    {r.urgency}
                  </span>
                  <span className="text-[10px] font-medium text-on-surface-variant">
                    #{r.id.slice(0, 6).toUpperCase()}
                  </span>
                </div>
                <h3 className="font-bold text-body-md mb-1">
                  {BLOOD_LABEL[r.blood_type]} · {r.units_needed} unit(s)
                </h3>
                <p className="text-[12px] text-on-surface-variant">
                  {r.hospital_name ?? r.city ?? "No location given"}
                </p>
                <p className="text-[11px] text-on-surface-variant mt-2 flex items-center gap-1">
                  <span aria-hidden="true" className="material-symbols-outlined text-[14px]">volunteer_activism</span>
                  {r.accepted_count} accepted so far
                </p>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function DirectoryForRequest({
  requestId,
  onChangeRequest,
}: {
  requestId: string;
  onChangeRequest: () => void;
}) {
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<AvailFilter>("ALL");

  const candidates = useQuery({
    queryKey: ["blood-requests", requestId, "match"],
    queryFn: () => getMatchCandidates(requestId),
  });
  const reqInfo = useQuery({
    queryKey: ["hospital", "requests", "picker"],
    queryFn: () => listHospitalRequests(true),
  });
  const request: HospitalRequestItem | undefined = reqInfo.data?.find((r) => r.id === requestId);

  const filtered = useMemo(() => {
    let list = candidates.data?.candidates ?? [];
    if (filter === "AVAILABLE") list = list.filter((c) => c.is_available);
    if (filter === "ACCEPTED") list = list.filter((c) => c.has_accepted);
    if (q.trim()) {
      const needle = q.trim().toLowerCase();
      list = list.filter(
        (c) =>
          c.donor_ref.toLowerCase().includes(needle) ||
          BLOOD_LABEL[c.blood_type].toLowerCase().includes(needle) ||
          (c.city ?? "").toLowerCase().includes(needle),
      );
    }
    return list;
  }, [candidates.data, filter, q]);

  return (
    <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
      <section className="xl:col-span-5 flex flex-col gap-4">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="font-headline-lg text-headline-lg text-on-surface truncate">
              {request ? `${BLOOD_LABEL[request.blood_type]} · ${request.units_needed} unit(s)` : "Compatible donors"}
            </h1>
            {candidates.data && (
              <p className="font-body-sm text-body-sm text-on-surface-variant mt-0.5">
                {candidates.data.candidates.length} compatible donor(s) · types{" "}
                {candidates.data.compatible_donor_types.map((b) => BLOOD_LABEL[b]).join(", ")}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onChangeRequest}
            className="shrink-0 px-3 py-1.5 rounded-lg bg-surface-container hover:bg-surface-container-high text-on-surface-variant font-label-sm text-label-sm font-semibold transition-colors"
          >
            Change request
          </button>
        </div>

        <div className="bg-surface-container-lowest rounded-xl p-4 shadow-sm border border-border-subtle flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <span aria-hidden="true" className="material-symbols-outlined absolute left-3 top-2.5 text-[20px] text-on-surface-variant">
                search
              </span>
              <input
                type="text"
                aria-label="Search donor reference, blood type, or city"
                className="w-full pl-10 pr-4 py-2 bg-surface-container-low rounded-lg font-body-sm text-body-sm text-on-surface placeholder:text-on-surface-variant outline-none focus:ring-2 focus:ring-primary/40"
                placeholder="Search donor ref, ABO/Rh, city…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            </div>
          </div>
          <div className="flex items-center gap-2 overflow-x-auto">
            {(["ALL", "AVAILABLE", "ACCEPTED"] as AvailFilter[]).map((f) => (
              <button
                key={f}
                type="button"
                aria-pressed={filter === f}
                onClick={() => setFilter(f)}
                className={`px-3 py-1.5 rounded-full font-label-sm text-label-sm font-semibold whitespace-nowrap transition-all ${
                  filter === f
                    ? "bg-primary text-on-primary shadow-sm"
                    : "bg-surface-container text-on-surface-variant hover:bg-surface-container-high"
                }`}
              >
                {f === "ALL" ? "All matching" : f === "AVAILABLE" ? "Available" : "Accepted"}
              </button>
            ))}
          </div>
        </div>

        {candidates.isLoading && (
          <div className="flex items-center gap-2 text-on-surface-variant py-8 justify-center">
            <Loader2 className="h-4 w-4 animate-spin" /> Scanning compatible donors…
          </div>
        )}
        {candidates.isError && (
          <div className="text-error text-body-sm flex items-center gap-3" role="alert">
            <span>{toApiError(candidates.error).detail}</span>
            <button type="button" className="underline flex-shrink-0" onClick={() => candidates.refetch()}>
              Retry
            </button>
          </div>
        )}
        {candidates.data && filtered.length === 0 && (
          <div className="rounded-xl border border-border-subtle bg-surface-container-lowest p-6 text-center text-on-surface-variant text-body-sm">
            No donors match this filter right now.
          </div>
        )}

        <ul className="flex flex-col gap-3">
          {filtered.map((c) => (
            <DonorCard key={c.donor_ref} c={c} />
          ))}
        </ul>
      </section>

      <section className="xl:col-span-7 flex flex-col gap-4">
        <div className="relative w-full h-[420px] sm:h-[560px] xl:h-[720px] rounded-2xl overflow-hidden shadow-md bg-surface-container-lowest border border-border-subtle">
          <DonorMap bloodType={request?.blood_type ?? candidates.data?.recipient_blood_type} />
        </div>
      </section>
    </div>
  );
}

export function DonorCard({ c }: { c: MatchCandidate }) {
  return (
    <li className="relative bg-surface-container-lowest rounded-xl p-4 sm:p-5 shadow-sm border border-border-subtle overflow-hidden">
      <div
        className={`absolute top-0 left-0 bottom-0 w-1.5 ${
          c.has_accepted ? "bg-success" : c.is_available ? "bg-secondary" : "bg-surface-container-high"
        }`}
      />
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-12 h-12 rounded-xl bg-surface-container text-on-surface flex items-center justify-center font-headline-sm text-headline-sm font-bold shrink-0">
            {BLOOD_LABEL[c.blood_type]}
          </div>
          <div className="flex flex-col min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-title-md text-title-md font-bold text-on-surface font-data-mono tracking-tight">
                Donor {c.donor_ref}
              </span>
              {c.has_accepted && (
                <span className="font-label-sm text-label-sm px-2 py-0.5 rounded-full bg-success/15 text-success font-semibold">
                  Accepted
                </span>
              )}
              {!c.has_accepted && (
                <span
                  className={`font-label-sm text-label-sm px-2 py-0.5 rounded-full font-semibold ${
                    c.is_available ? "bg-secondary-fixed text-on-secondary-fixed" : "bg-surface-container text-on-surface-variant"
                  }`}
                >
                  {c.is_available ? "Available" : "Not available"}
                </span>
              )}
            </div>
            <span className="font-body-sm text-body-sm text-on-surface-variant mt-0.5 truncate">
              {c.city ?? "City not shared"}
              {c.last_donation_date ? ` · Last donation ${c.last_donation_date}` : " · No prior donation on record"}
            </span>
          </div>
        </div>
        <div className="text-right shrink-0">
          {c.distance_km != null ? (
            <>
              <span className="font-label-sm text-label-sm font-data-mono text-secondary font-bold">
                ~{c.distance_km} km
              </span>
              <p className="font-label-sm text-label-sm text-on-surface-variant">Coarsened distance</p>
            </>
          ) : (
            <span className="font-label-sm text-label-sm text-on-surface-variant">Location not shared</span>
          )}
        </div>
      </div>
    </li>
  );
}
