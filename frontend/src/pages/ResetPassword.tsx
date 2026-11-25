import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowRight, CheckCircle2, Eye, EyeOff, Loader2, Lock } from "lucide-react";

import AuthShell, { inputClass, primaryBtnClass } from "../components/AuthShell";
import { api, toApiError } from "../lib/api";

export default function ResetPassword() {
  const [params] = useSearchParams();
  const navigate = useNavigate();

  const [email, setEmail] = useState(params.get("email") ?? "");
  const [otp, setOtp] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await api.post("/auth/reset-password", {
        email,
        otp,
        new_password: password,
      });
      setDone(true);
    } catch (err) {
      setError(toApiError(err).detail);
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <AuthShell title="Password updated" subtitle="You've been signed out of all devices.">
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-3 text-success">
            <CheckCircle2 className="h-6 w-6" />
            <span className="font-label-md text-label-md">All set.</span>
          </div>
          <button className={primaryBtnClass} onClick={() => navigate("/login")}>
            Sign in <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Choose a new password"
      subtitle="Enter the code we emailed you and a new password (at least 8 characters)."
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <label htmlFor="reset-email" className="sr-only">
          Email address
        </label>
        <input
          id="reset-email"
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email address"
          autoComplete="email"
          className={inputClass}
        />
        <label htmlFor="reset-otp" className="sr-only">
          6-digit code
        </label>
        <input
          id="reset-otp"
          inputMode="numeric"
          pattern="\d{6}"
          maxLength={6}
          required
          value={otp}
          onChange={(e) => setOtp(e.target.value.replace(/\D/g, ""))}
          placeholder="6-digit code"
          autoComplete="one-time-code"
          className={`${inputClass} tracking-[0.4em] text-center`}
        />
        <div className="relative">
          <label htmlFor="reset-password" className="sr-only">
            New password
          </label>
          <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-on-surface-variant" />
          <input
            id="reset-password"
            type={showPassword ? "text" : "password"}
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="New password"
            autoComplete="new-password"
            className={`${inputClass} pl-11 pr-11`}
          />
          <button
            type="button"
            onClick={() => setShowPassword((s) => !s)}
            aria-label={showPassword ? "Hide password" : "Show password"}
            className="absolute right-3.5 top-1/2 -translate-y-1/2 text-on-surface-variant hover:text-primary"
            tabIndex={-1}
          >
            {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
        {error && <p className="text-error text-body-sm">{error}</p>}
        <button type="submit" disabled={submitting} className={primaryBtnClass}>
          {submitting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> Updating…
            </>
          ) : (
            <>
              Update password <ArrowRight className="h-4 w-4" />
            </>
          )}
        </button>
      </form>
      <div className="mt-4 flex gap-4 text-body-sm">
        <Link to="/forgot-password" className="text-secondary hover:underline">
          Request a new code
        </Link>
        <Link to="/login" className="text-secondary hover:underline">
          Back to sign in
        </Link>
      </div>
    </AuthShell>
  );
}
