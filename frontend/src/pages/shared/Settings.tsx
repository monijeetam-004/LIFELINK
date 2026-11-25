/**
 * Shared Settings
 * Adapted from: lifelink_0.3_shared_settings/code.html
 *
 * Account name/email editing still has no backing endpoint (auth.py has no
 * PUT /auth/me), so that section stays an honest read-only display. Password
 * change (POST /auth/change-password) and notification preferences
 * (GET/PUT /notifications/preferences) are now real and wired below. Donor
 * availability remains the other real, working toggle (PUT /donors/me/availability).
 */
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";

import AvailabilitySelector from "../../components/AvailabilitySelector";
import NotificationsBell from "../../components/NotificationsBell";
import { fieldClass, labelClass, primaryButtonClass } from "../../components/Modal";
import { toApiError } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import {
  changePassword,
  getDonorProfile,
  getNotificationPreferences,
  setAvailability,
  updateDonorProfile,
  updateNotificationPreferences,
  type DonorAvailabilityState,
  type NotificationDigestFrequency,
  type NotificationPreference,
  type Urgency,
} from "../../lib/domain";

function ToggleSwitch({
  on,
  onClick,
  disabled,
  label,
}: {
  on: boolean;
  onClick: () => void;
  disabled?: boolean;
  /** Accessible name -- this control has no visible text of its own (just a
   * sliding thumb), so screen-reader users need it stated explicitly rather
   * than inferred from surrounding text. */
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={on}
      aria-label={`${label}: ${on ? "on" : "off"}`}
      className={`relative w-12 h-7 rounded-full transition-colors disabled:opacity-60 ${
        on ? "bg-success" : "bg-surface-container-high"
      }`}
    >
      <span
        aria-hidden="true"
        className={`absolute top-1 left-1 w-5 h-5 rounded-full bg-white shadow transition-transform ${
          on ? "translate-x-5" : ""
        }`}
      />
    </button>
  );
}

function PasswordSection() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [formError, setFormError] = useState("");

  const changePw = useMutation({
    mutationFn: () => changePassword(current, next),
    onSuccess: () => {
      setCurrent("");
      setNext("");
      setConfirm("");
      setFormError("");
    },
  });

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError("");
    changePw.reset();
    if (!current || !next || !confirm) {
      setFormError("Fill in all three fields.");
      return;
    }
    if (next !== confirm) {
      setFormError("New password and confirmation don't match.");
      return;
    }
    changePw.mutate();
  }

  return (
    <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 space-y-3">
      <h2 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs">
        B. Password
      </h2>
      <form onSubmit={onSubmit} className="space-y-3">
        <div>
          <label className={labelClass} htmlFor="settings-current-password">Current password</label>
          <input
            id="settings-current-password"
            type="password"
            autoComplete="current-password"
            className={fieldClass}
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className={labelClass} htmlFor="settings-new-password">New password</label>
            <input
              id="settings-new-password"
              type="password"
              autoComplete="new-password"
              className={fieldClass}
              value={next}
              onChange={(e) => setNext(e.target.value)}
            />
          </div>
          <div>
            <label className={labelClass} htmlFor="settings-confirm-password">Confirm new password</label>
            <input
              id="settings-confirm-password"
              type="password"
              autoComplete="new-password"
              className={fieldClass}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </div>
        </div>
        {formError && <p className="text-error text-sm">{formError}</p>}
        {changePw.isError && <p className="text-error text-sm">{toApiError(changePw.error).detail}</p>}
        {changePw.isSuccess && <p className="text-success text-sm">Password changed.</p>}
        <button type="submit" className={primaryButtonClass} disabled={changePw.isPending}>
          {changePw.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Change password
        </button>
      </form>
      <div className="pt-1 border-t border-border-subtle mt-1">
        <p className="text-xs text-on-surface-variant mt-3 mb-2">Forgot your current password?</p>
        <ForgotPasswordLink />
      </div>
    </section>
  );
}

function ForgotPasswordLink() {
  const navigate = useNavigate();
  return (
    <button
      onClick={() => navigate("/forgot-password")}
      className="border border-border-subtle text-on-surface font-label-md text-label-md px-5 py-2.5 rounded-lg hover:bg-surface-container-low transition-colors"
    >
      Reset my password by email
    </button>
  );
}

const DIGEST_OPTIONS: { value: NotificationDigestFrequency; label: string }[] = [
  { value: "REALTIME", label: "Realtime" },
  { value: "DAILY", label: "Daily digest" },
  { value: "OFF", label: "Off" },
];

function NotificationPreferencesSection() {
  const qc = useQueryClient();
  const prefs = useQuery({ queryKey: ["notifications", "preferences"], queryFn: getNotificationPreferences });
  const [saveError, setSaveError] = useState("");
  const [savedAt, setSavedAt] = useState(0);

  const save = useMutation({
    mutationFn: (body: Partial<NotificationPreference>) => updateNotificationPreferences(body),
    onSuccess: (data) => {
      qc.setQueryData(["notifications", "preferences"], data);
      setSaveError("");
      setSavedAt(Date.now());
    },
    onError: (e) => setSaveError(toApiError(e).detail),
  });

  const p = prefs.data;

  function toggle(field: keyof NotificationPreference) {
    if (!p) return;
    save.mutate({ [field]: !p[field] } as Partial<NotificationPreference>);
  }

  function setDigest(freq: NotificationDigestFrequency) {
    save.mutate({ digest_frequency: freq });
  }

  function setQuietHour(field: "quiet_hours_start" | "quiet_hours_end", value: string) {
    save.mutate({ [field]: value || null } as Partial<NotificationPreference>);
  }

  return (
    <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 space-y-4">
      <h2 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs">
        C. Notification preferences
      </h2>
      <p className="text-sm text-on-surface-variant">
        In-app notifications (the bell icon) are always on. The channel and quiet hours settings below
        control email delivery and are saved to your account.
      </p>

      {prefs.isLoading && <p className="text-sm text-on-surface-variant">Loading…</p>}
      {prefs.isError && <p className="text-sm text-error">{toApiError(prefs.error).detail}</p>}

      {p && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <p className="font-label-md text-label-md text-on-surface">Email notifications</p>
              <p className="text-xs text-on-surface-variant">Request updates and match alerts by email.</p>
            </div>
            <ToggleSwitch
              on={p.email_enabled}
              onClick={() => toggle("email_enabled")}
              disabled={save.isPending}
              label="Email notifications"
            />
          </div>
          {/* SMS is not offered as a user-facing control: this deployment has no
              SMS provider configured, and showing a toggle here would imply a
              delivery channel that doesn't actually work. See
              LIFELINK_SMS_STATUS.md. The backend keeps
              NotificationPreference.sms_enabled as a dormant, defaults-off
              capability flag for a future deployment that configures one. */}
          <p className="text-xs text-on-surface-variant italic">
            SMS notifications are not currently enabled for this deployment.
          </p>
          <div className="flex items-center justify-between">
            <div>
              <p className="font-label-md text-label-md text-on-surface">Emergency alerts</p>
              <p className="text-xs text-on-surface-variant">Always-on critical alerts, recommended for donors.</p>
            </div>
            <ToggleSwitch
              on={p.emergency_alerts_enabled}
              onClick={() => toggle("emergency_alerts_enabled")}
              disabled={save.isPending}
              label="Emergency alerts"
            />
          </div>

          <div>
            <label className={labelClass} htmlFor="settings-digest-frequency">Digest frequency</label>
            <select
              id="settings-digest-frequency"
              className={fieldClass}
              value={p.digest_frequency}
              disabled={save.isPending}
              onChange={(e) => setDigest(e.target.value as NotificationDigestFrequency)}
            >
              {DIGEST_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className={labelClass} htmlFor="settings-quiet-start">Quiet hours start</label>
              <input
                id="settings-quiet-start"
                type="time"
                className={fieldClass}
                disabled={save.isPending}
                value={p.quiet_hours_start ?? ""}
                onChange={(e) => setQuietHour("quiet_hours_start", e.target.value)}
              />
            </div>
            <div>
              <label className={labelClass} htmlFor="settings-quiet-end">Quiet hours end</label>
              <input
                id="settings-quiet-end"
                type="time"
                className={fieldClass}
                disabled={save.isPending}
                value={p.quiet_hours_end ?? ""}
                onChange={(e) => setQuietHour("quiet_hours_end", e.target.value)}
              />
            </div>
          </div>

          {saveError && <p className="text-error text-sm">{saveError}</p>}
          {save.isPending && <p className="text-xs text-on-surface-variant">Saving…</p>}
          {!save.isPending && savedAt > 0 && !saveError && (
            <p className="text-xs text-success">Saved.</p>
          )}
        </div>
      )}
    </section>
  );
}

const URGENCY_OPTIONS: { value: Urgency; label: string }[] = [
  { value: "ROUTINE", label: "Every request (routine and up)" },
  { value: "URGENT", label: "Urgent and critical only" },
  { value: "CRITICAL", label: "Critical only" },
];

export default function Settings() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const donorProfile = useQuery({
    queryKey: ["donor", "me"],
    queryFn: getDonorProfile,
    enabled: user?.role === "DONOR",
  });
  // Phase 21: all three donor-settings mutations below used to have no
  // onError handler at all -- a failed save (network blip, 500, session
  // expiring mid-toggle) silently reverted the control on next fetch with
  // no explanation. `donorSettingsError` is a single shared slot rendered
  // once near the donor sections, since only one of these controls is ever
  // realistically in flight at a time and they all edit the same
  // donor-profile record.
  const [donorSettingsError, setDonorSettingsError] = useState("");
  const [geoError, setGeoError] = useState("");
  const setAvailabilityMut = useMutation({
    mutationFn: (state: DonorAvailabilityState) => setAvailability(state),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["donor", "me"] });
      setDonorSettingsError("");
    },
    onError: (e) => setDonorSettingsError(toApiError(e).detail),
  });
  const locationMut = useMutation({
    mutationFn: (body: {
      share_location: boolean;
      latitude?: number | null;
      longitude?: number | null;
    }) => updateDonorProfile(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["donor", "me"] });
      setDonorSettingsError("");
    },
    onError: (e) => setDonorSettingsError(toApiError(e).detail),
  });
  const alertPrefsMut = useMutation({
    mutationFn: (body: { alert_min_urgency?: Urgency; alert_radius_km?: number | null }) =>
      updateDonorProfile(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["donor", "me"] });
      setDonorSettingsError("");
    },
    onError: (e) => setDonorSettingsError(toApiError(e).detail),
  });

  function toggleLocationSharing() {
    setGeoError("");
    if (donorProfile.data?.share_location) {
      locationMut.mutate({ share_location: false, latitude: null, longitude: null });
      return;
    }
    if (!navigator.geolocation) {
      setGeoError("Your browser doesn't support location sharing.");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) =>
        locationMut.mutate({
          share_location: true,
          // Stored coarse: the server also rounds before publishing to any map.
          latitude: Math.round(pos.coords.latitude * 100) / 100,
          longitude: Math.round(pos.coords.longitude * 100) / 100,
        }),
      (geoErr) => {
        locationMut.reset();
        // Phase 21 fix: this callback used to be a silent no-op -- a denied
        // permission or a location timeout left the toggle looking like it
        // simply did nothing, indistinguishable from the click not
        // registering at all.
        setGeoError(
          geoErr.code === geoErr.PERMISSION_DENIED
            ? "Location permission was denied. Allow location access in your browser to share it."
            : "Couldn't get your location. Check your device's location settings and try again.",
        );
      },
      { enableHighAccuracy: false, timeout: 8000 },
    );
  }

  if (!user) return null;

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
          <h1 className="font-headline-md text-headline-md text-on-surface">Account Settings</h1>
        </div>
        <NotificationsBell buttonClassName="relative p-2 text-on-surface-variant hover:bg-surface-container rounded-full transition-all" />
      </header>

      <main className="max-w-2xl mx-auto p-margin-mobile md:p-margin-desktop space-y-6">
        <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 space-y-3">
          <h2 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs">
            A. Primary account credentials
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm opacity-70">
            <div>
              <label className="block text-xs text-on-surface-variant mb-1" htmlFor="settings-full-name">Full name</label>
              <input id="settings-full-name" disabled className="w-full px-3.5 py-2.5 rounded-lg border border-outline-variant bg-surface-container-low text-on-surface cursor-not-allowed" value={user.full_name} readOnly />
            </div>
            <div>
              <label className="block text-xs text-on-surface-variant mb-1" htmlFor="settings-email">Email</label>
              <input id="settings-email" disabled className="w-full px-3.5 py-2.5 rounded-lg border border-outline-variant bg-surface-container-low text-on-surface cursor-not-allowed" value={user.email} readOnly />
            </div>
          </div>
          <p className="text-xs text-on-surface-variant">
            Editing your name or email isn't supported by the server yet. Contact LifeLink support if either
            needs to change.
          </p>
        </section>

        <PasswordSection />

        <NotificationPreferencesSection />

        {user.role === "DONOR" && donorProfile.isError && (
          <div role="alert" className="rounded-xl border border-error/30 bg-error/5 p-4 text-body-sm text-error">
            <p>Couldn't load your donor settings. {toApiError(donorProfile.error).detail}</p>
            <button
              type="button"
              onClick={() => donorProfile.refetch()}
              className="mt-2 text-secondary font-label-md hover:underline"
            >
              Retry
            </button>
          </div>
        )}
        {user.role === "DONOR" && (donorSettingsError || geoError) && (
          <p role="alert" className="text-error text-sm">
            {donorSettingsError || geoError}
          </p>
        )}

        {user.role === "DONOR" && (
          <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 space-y-3">
            <h2 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs">
              D. Donor availability
            </h2>
            <div className="space-y-2">
              <p className="font-label-md text-label-md text-on-surface">
                {donorProfile.data?.availability_state === "AVAILABLE"
                  ? "Available to donate"
                  : donorProfile.data?.availability_state === "NOT_NOW"
                    ? "Not right now"
                    : "Unavailable"}
              </p>
              <p className="text-xs text-on-surface-variant">
                Available is the only state that shows you as a match for open requests and
                emergencies. Not now and Unavailable both take you out of matching — Not now is
                for a short break, Unavailable is longer-term.
              </p>
              <AvailabilitySelector
                value={donorProfile.data?.availability_state}
                onChange={(state) => setAvailabilityMut.mutate(state)}
                disabled={donorProfile.isLoading || setAvailabilityMut.isPending}
              />
            </div>
          </section>
        )}

        {user.role === "DONOR" && (
          <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 space-y-4">
            <h2 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs">
              E. Alert watcher
            </h2>
            <p className="text-sm text-on-surface-variant">
              Filters on top of the automatic blood-type match every request and emergency
              already goes through. There's no blood-group/component picker here — you have one
              blood type on file and LifeLink only tracks whole-blood donations, so a selector
              would have nothing real behind it.
            </p>
            <div>
              <label className={labelClass} htmlFor="settings-alert-urgency">Notify me for</label>
              <select
                id="settings-alert-urgency"
                className={fieldClass}
                value={donorProfile.data?.alert_min_urgency ?? "ROUTINE"}
                disabled={donorProfile.isLoading || alertPrefsMut.isPending}
                onChange={(e) =>
                  alertPrefsMut.mutate({ alert_min_urgency: e.target.value as Urgency })
                }
              >
                {URGENCY_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
              <p className="text-xs text-on-surface-variant mt-1">
                An active emergency SOS always reaches you regardless of this setting.
              </p>
            </div>
            <div>
              <label className={labelClass} htmlFor="settings-alert-radius">Alert radius (km)</label>
              <input
                id="settings-alert-radius"
                type="number"
                min={1}
                max={500}
                placeholder="No limit"
                className={fieldClass}
                defaultValue={donorProfile.data?.alert_radius_km ?? ""}
                disabled={donorProfile.isLoading || alertPrefsMut.isPending}
                onBlur={(e) => {
                  const raw = e.target.value.trim();
                  alertPrefsMut.mutate({ alert_radius_km: raw === "" ? null : Number(raw) });
                }}
              />
              <p className="text-xs text-on-surface-variant mt-1">
                Only applies once you're sharing your approximate location below — otherwise a
                real distance can't be computed, so you're notified regardless of this radius.
              </p>
            </div>
            <div className="flex items-center justify-between pt-2 border-t border-border-subtle">
              <div>
                <p className="font-label-md text-label-md text-on-surface">
                  Share approximate location
                </p>
                <p className="text-xs text-on-surface-variant">
                  Coarse (~1km) location, used only to compute the radius above and shown to
                  hospitals as an approximate map pin — never your exact address.
                </p>
              </div>
              <ToggleSwitch
                on={!!donorProfile.data?.share_location}
                onClick={toggleLocationSharing}
                disabled={donorProfile.isLoading || locationMut.isPending}
                label="Share approximate location"
              />
            </div>
          </section>
        )}

        {user.role === "HOSPITAL" && (
          <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 space-y-2">
            <h2 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs">
              D. Facility operations
            </h2>
            <Link to="/hospital" className="text-secondary text-sm font-label-md hover:underline">
              Manage inventory & incoming requests in the Hospital Dashboard →
            </Link>
          </section>
        )}

        {user.role === "FAMILY" && (
          <section className="bg-surface rounded-xl shadow-soft border border-border-subtle p-6 space-y-2">
            <h2 className="font-label-md text-on-surface-variant uppercase tracking-widest text-xs">
              D. Linked patients
            </h2>
            <Link to="/family/patients" className="text-secondary text-sm font-label-md hover:underline">
              Manage linked patients →
            </Link>
          </section>
        )}

        <section className="bg-surface rounded-xl shadow-soft border border-error/20 p-6">
          <button
            onClick={() => logout().then(() => navigate("/login", { replace: true }))}
            className="text-error font-label-md text-label-md hover:underline flex items-center gap-2"
          >
            <span aria-hidden="true" className="material-symbols-outlined text-[18px]">logout</span>
            Sign out
          </button>
        </section>
      </main>
    </div>
  );
}
