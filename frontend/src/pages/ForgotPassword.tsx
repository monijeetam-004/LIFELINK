import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, Loader2, MailQuestion } from "lucide-react";

import AuthShell, { inputClass, primaryBtnClass } from "../components/AuthShell";
import { api, toApiError } from "../lib/api";

export default function ForgotPassword() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const { data } = await api.post<{ message: string }>("/auth/forgot-password", { email });
      setMessage(data.message);
    } catch (err) {
      setError(toApiError(err).detail);
    } finally {
      setSubmitting(false);
    }
  }

  if (message) {
    return (
      <AuthShell title="Check your email" subtitle={message}>
        <div className="flex flex-col gap-4">
          <p className="text-on-surface-variant text-body-sm">
            Enter the 6-digit code from that email on the next screen to choose a new password.
          </p>
          <button
            className={primaryBtnClass}
            onClick={() => navigate(`/reset-password?email=${encodeURIComponent(email)}`)}
          >
            Enter reset code <ArrowRight className="h-4 w-4" />
          </button>
          <Link to="/login" className="text-secondary text-body-sm hover:underline">
            Back to sign in
          </Link>
        </div>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      title="Reset your password"
      subtitle="Enter your account email and we'll send you a one-time code."
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-3">
        <div className="relative">
          <label htmlFor="forgot-email" className="sr-only">
            Email address
          </label>
          <MailQuestion className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-on-surface-variant" />
          <input
            id="forgot-email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Email address"
            autoComplete="email"
            className={`${inputClass} pl-11`}
          />
        </div>
        {error && <p className="text-error text-body-sm">{error}</p>}
        <button type="submit" disabled={submitting} className={primaryBtnClass}>
          {submitting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" /> Sending…
            </>
          ) : (
            <>
              Send reset code <ArrowRight className="h-4 w-4" />
            </>
          )}
        </button>
      </form>
      <Link to="/login" className="mt-4 inline-block text-secondary text-body-sm hover:underline">
        Back to sign in
      </Link>
    </AuthShell>
  );
}
