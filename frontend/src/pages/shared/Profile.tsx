/**
 * Role-aware Profile
 * Adapted from: lifelink_0.2_role_aware_profile/code.html
 *
 * One profile page shared by every role, showing only the fields that role's backend
 * profile actually has. FAMILY intentionally does NOT get patient-profile editing here —
 * a family member is not a patient, and /patients/me would silently create a bogus patient
 * profile keyed on their own account if called. Family identity is account-only plus a
 * link to their linked patients.
 */
import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";

import NotificationsBell from "../../components/NotificationsBell";
import { fieldClass, labelClass, primaryButtonClass } from "../../components/Modal";
import { useAuth } from "../../lib/auth";
import { toApiError } from "../../lib/api";
import { ROLE_LABEL } from "../../lib/roles";
import {
  BLOOD_LABEL,
  BLOOD_TYPES,
  getDonorProfile,
  getHospitalProfile,
  getPatientProfile,
  listFamilyLinks,
  updateHospitalProfile,
  updatePatientProfile,
  type BloodType,
} from "../../lib/domain";

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-30 h-16 bg-surface/90 backdrop-blur-md border-b border-border-subtle flex items-center justify-between px-margin-mobile md:px-margin-desktop">
        <div className="flex items-center gap-4">
          <Link
            to="/"
            aria-label="Back to dashboard"
            className="p-2 rounded-full hover:bg-surface-container-low text-on-surface-variant"
          >
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <h1 className="font-headline-md text-headline-md text-on-surface">Profile</h1>
        </div>
        <NotificationsBell buttonClassName="relative p-2 text-on-surface-variant hover:bg-surface-container rounded-full transition-all" />
      </header>
      <main className="max-w-2xl mx-auto p-margin-mobile md:p-margin-desktop space-y-6">{children}</main>
    </div>
  );
}

export default function Profile() {
  const { user } = useAuth();
  if (!user) return null;

  return (
    <Shell>
      <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 flex items-center gap-4">
        <div className="w-16 h-16 rounded-full bg-primary/10 text-primary flex items-center justify-center flex-shrink-0">
          <span aria-hidden="true" className="material-symbols-outlined text-3xl">account_circle</span>
        </div>
        <div className="min-w-0">
          <h2 className="font-headline-sm text-headline-sm text-on-surface truncate">{user.full_name}</h2>
          <p className="text-sm text-on-surface-variant truncate">{user.email}</p>
          <div className="flex items-center gap-2 mt-1.5">
            <span className="px-2.5 py-0.5 rounded-full bg-primary/10 text-primary text-xs font-bold">
              {ROLE_LABEL[user.role]}
            </span>
            <span
              className={`px-2.5 py-0.5 rounded-full text-xs font-bold ${
                user.is_email_verified ? "bg-success/15 text-success" : "bg-warning/15 text-warning"
              }`}
            >
              {user.is_email_verified ? "Email verified" : "Email not verified"}
            </span>
          </div>
        </div>
      </section>

      {user.role === "PATIENT" && <PatientFields />}
      {user.role === "DONOR" && <DonorFields />}
      {user.role === "HOSPITAL" && <HospitalFields />}
      {user.role === "FAMILY" && <FamilyFields />}
      {user.role === "ADMIN" && (
        <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6">
          <p className="text-sm text-on-surface-variant">
            Admin accounts don't have an extended profile — only account identity above.
          </p>
        </section>
      )}
    </Shell>
  );
}

function FamilyFields() {
  const links = useQuery({ queryKey: ["family", "links"], queryFn: listFamilyLinks });
  const accepted = (links.data ?? []).filter((l) => l.status === "ACCEPTED");
  return (
    <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6">
      <h3 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs mb-3">
        Family & Attendant Care
      </h3>
      {links.isError ? (
        <div role="alert" className="text-sm text-error mb-4">
          <p>Couldn't load your linked patients. {toApiError(links.error).detail}</p>
          <button
            type="button"
            onClick={() => links.refetch()}
            className="mt-2 text-secondary font-label-md hover:underline"
          >
            Retry
          </button>
        </div>
      ) : (
        <p className="text-sm text-on-surface-variant mb-4">
          {links.isLoading
            ? "Loading your linked patients…"
            : `You're linked to ${accepted.length} patient${accepted.length === 1 ? "" : "s"}.`}{" "}
          Family accounts don't carry their own medical profile — manage the people you support from the
          Family portal.
        </p>
      )}
      <Link to="/family/patients" className="text-secondary text-sm font-label-md hover:underline">
        Manage linked patients →
      </Link>
    </section>
  );
}

function PatientFields() {
  const qc = useQueryClient();
  const profile = useQuery({ queryKey: ["patient", "me"], queryFn: getPatientProfile });
  const [form, setForm] = useState<Record<string, string>>({});
  const [error, setError] = useState("");

  const save = useMutation({
    mutationFn: () =>
      updatePatientProfile({
        blood_type: (form.blood_type || null) as BloodType | null,
        date_of_birth: form.date_of_birth || null,
        phone: form.phone || null,
        address: form.address || null,
        city: form.city || null,
        emergency_contact_name: form.emergency_contact_name || null,
        emergency_contact_phone: form.emergency_contact_phone || null,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["patient", "me"] }),
    onError: (e) => setError(toApiError(e).detail),
  });

  const p = profile.data;
  const val = (k: string, fallback: string | null | undefined) => form[k] ?? fallback ?? "";

  if (profile.isLoading) return <p className="text-on-surface-variant text-center">Loading…</p>;
  if (profile.isError) {
    return (
      <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 text-center" role="alert">
        <p className="text-error text-sm">Couldn't load your clinical profile. {toApiError(profile.error).detail}</p>
        <button
          type="button"
          onClick={() => profile.refetch()}
          className="mt-2 text-secondary text-sm font-label-md hover:underline"
        >
          Retry
        </button>
      </section>
    );
  }

  return (
    <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 space-y-4">
      <h3 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs">
        Patient Clinical Context
      </h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className={labelClass} htmlFor="profile-blood-group">Blood group</label>
          <select
            id="profile-blood-group"
            className={fieldClass}
            value={val("blood_type", p?.blood_type ?? "")}
            onChange={(e) => setForm((f) => ({ ...f, blood_type: e.target.value }))}
          >
            <option value="">Unknown</option>
            {BLOOD_TYPES.map((b) => (
              <option key={b} value={b}>{BLOOD_LABEL[b]}</option>
            ))}
          </select>
        </div>
        <div>
          <label className={labelClass} htmlFor="profile-dob">Date of birth</label>
          <input id="profile-dob" type="date" className={fieldClass} value={val("date_of_birth", p?.date_of_birth)} onChange={(e) => setForm((f) => ({ ...f, date_of_birth: e.target.value }))} />
        </div>
        <div>
          <label className={labelClass} htmlFor="profile-phone">Phone</label>
          <input id="profile-phone" className={fieldClass} value={val("phone", p?.phone)} onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))} />
        </div>
        <div>
          <label className={labelClass} htmlFor="profile-city">City</label>
          <input id="profile-city" className={fieldClass} value={val("city", p?.city)} onChange={(e) => setForm((f) => ({ ...f, city: e.target.value }))} />
        </div>
        <div className="col-span-2">
          <label className={labelClass} htmlFor="profile-address">Address</label>
          <input id="profile-address" className={fieldClass} value={val("address", p?.address)} onChange={(e) => setForm((f) => ({ ...f, address: e.target.value }))} />
        </div>
        <div>
          <label className={labelClass} htmlFor="profile-ec-name">Emergency contact name</label>
          <input id="profile-ec-name" className={fieldClass} value={val("emergency_contact_name", p?.emergency_contact_name)} onChange={(e) => setForm((f) => ({ ...f, emergency_contact_name: e.target.value }))} />
        </div>
        <div>
          <label className={labelClass} htmlFor="profile-ec-phone">Emergency contact phone</label>
          <input id="profile-ec-phone" className={fieldClass} value={val("emergency_contact_phone", p?.emergency_contact_phone)} onChange={(e) => setForm((f) => ({ ...f, emergency_contact_phone: e.target.value }))} />
        </div>
      </div>
      {error && <p className="text-error text-sm" role="alert">{error}</p>}
      <div className="flex justify-end">
        <button className={primaryButtonClass} onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Save changes
        </button>
      </div>
    </section>
  );
}

function DonorFields() {
  const profile = useQuery({ queryKey: ["donor", "me"], queryFn: getDonorProfile });
  const p = profile.data;
  if (profile.isLoading) return <p className="text-on-surface-variant text-center">Loading…</p>;
  if (profile.isError) {
    return (
      <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 text-center" role="alert">
        <p className="text-error text-sm">Couldn't load your donor registration. {toApiError(profile.error).detail}</p>
        <button
          type="button"
          onClick={() => profile.refetch()}
          className="mt-2 text-secondary text-sm font-label-md hover:underline"
        >
          Retry
        </button>
      </section>
    );
  }
  return (
    <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 space-y-3">
      <h3 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs">
        Donor Registration
      </h3>
      <div className="grid grid-cols-2 gap-3 text-sm">
        <div><span className="text-on-surface-variant">Blood group</span><p className="font-label-md">{p?.blood_type ? BLOOD_LABEL[p.blood_type] : "Not set"}</p></div>
        <div><span className="text-on-surface-variant">City</span><p className="font-label-md">{p?.city || "Not set"}</p></div>
        <div><span className="text-on-surface-variant">Availability</span><p className="font-label-md">{p?.is_available ? "Available" : "Unavailable"}</p></div>
        <div><span className="text-on-surface-variant">Eligible to donate</span><p className="font-label-md">{p?.eligible ? "Yes" : `From ${p?.eligible_on ?? "—"}`}</p></div>
      </div>
      <Link to="/donor" className="text-secondary text-sm font-label-md hover:underline inline-block pt-2">
        Edit full donor profile in the Donor Dashboard →
      </Link>
    </section>
  );
}

function HospitalFields() {
  const qc = useQueryClient();
  const profile = useQuery({ queryKey: ["hospital", "me"], queryFn: getHospitalProfile });
  const [form, setForm] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const p = profile.data;
  const val = (k: string, fallback: string | null | undefined) => form[k] ?? fallback ?? "";
  const save = useMutation({
    mutationFn: () =>
      updateHospitalProfile({
        name: form.name ?? p?.name,
        phone: form.phone ?? p?.phone,
        address: form.address ?? p?.address,
        city: form.city ?? p?.city,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hospital", "me"] });
      setError("");
    },
    onError: (e) => setError(toApiError(e).detail),
  });
  if (profile.isLoading) return <p className="text-on-surface-variant text-center">Loading…</p>;
  if (profile.isError) {
    return (
      <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 text-center" role="alert">
        <p className="text-error text-sm">Couldn't load your facility registration. {toApiError(profile.error).detail}</p>
        <button
          type="button"
          onClick={() => profile.refetch()}
          className="mt-2 text-secondary text-sm font-label-md hover:underline"
        >
          Retry
        </button>
      </section>
    );
  }
  return (
    <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 space-y-4">
      <h3 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs flex items-center gap-2">
        Facility Registration
        <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold ${p?.verified ? "bg-success/15 text-success" : "bg-warning/15 text-warning"}`}>
          {p?.verified ? "Verified" : "Pending verification"}
        </span>
      </h3>
      <div className="grid grid-cols-2 gap-4">
        <div className="col-span-2">
          <label className={labelClass} htmlFor="profile-hospital-name">Facility name</label>
          <input id="profile-hospital-name" className={fieldClass} value={val("name", p?.name)} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        </div>
        <div>
          <label className={labelClass} htmlFor="profile-hospital-phone">Phone</label>
          <input id="profile-hospital-phone" className={fieldClass} value={val("phone", p?.phone)} onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))} />
        </div>
        <div>
          <label className={labelClass} htmlFor="profile-hospital-city">City</label>
          <input id="profile-hospital-city" className={fieldClass} value={val("city", p?.city)} onChange={(e) => setForm((f) => ({ ...f, city: e.target.value }))} />
        </div>
        <div className="col-span-2">
          <label className={labelClass} htmlFor="profile-hospital-address">Address</label>
          <input id="profile-hospital-address" className={fieldClass} value={val("address", p?.address)} onChange={(e) => setForm((f) => ({ ...f, address: e.target.value }))} />
        </div>
      </div>
      <p className="text-xs text-on-surface-variant">
        License number can't be changed here for verification integrity. Contact LifeLink support to update it.
      </p>
      {error && <p className="text-error text-sm" role="alert">{error}</p>}
      <div className="flex justify-end">
        <button className={primaryButtonClass} onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Save changes
        </button>
      </div>
    </section>
  );
}
