import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { CheckCircle2, XCircle, Loader2, MailCheck, ArrowRight } from "lucide-react";

import AuthShell, { inputClass, primaryBtnClass } from "../components/AuthShell";
import { api, toApiError } from "../lib/api";
import { useAuth } from "../lib/auth";

type State = "idle" | "verifying" | "success" | "error";

export default function VerifyEmail() {
  const [params] = useSearchParams();
  const token = params.get("token");
  const navigate = useNavigate();
  const { refresh } = useAuth();

  const [state, setState] = useState<State>(token ? "verifying" : "idle");
  const [message, setMessage] = useState("");
  const [email, setEmail] = useState("");
  const [resent, setResent] = useState(false);
  const ran = useRef(false);

  useEffect(() => {
    if (!token || ran.current) return;
    ran.current = true;
    (async () => {
      try {
        const { data } = await api.post<{ message: string }>("/auth/verify-email", { token });
        setMessage(data.message);
        setState("success");
        void refresh();
      } catch (err) {
        setMessage(toApiError(err).detail);
        setState("error");
      }
    })();
  }, [token, refresh]);

  async function handleResend(e: React.FormEvent) {
    e.preventDefault();
    setResent(false);
    try {
      const { data } = await api.post<{ message: string }>("/auth/verify-email/resend", {
        email,
      });
      setMessage(data.message);
      setResent(true);
    } catch (err) {
      setMessage(toApiError(err).detail);
    }
  }

  if (state === "verifying") {
    return (
      <AuthShell title="Verifying your email…">
        <div className="flex items-center gap-3 text-on-surface-variant">
          <Loader2 className="h-5 w-5 animate-spin text-primary" />
          One moment.
        </div>
      </AuthShell>
    );
  }

  if (state === "success") {
    return (
      <AuthShell title="Email verified" subtitle={message}>
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-3 text-success">
            <CheckCircle2 className="h-6 w-6" />
            <span className="font-label-md text-label-md">You're all set.</span>
          </div>
          <button className={primaryBtnClass} onClick={() => navigate("/login")}>
            Continue to sign in <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </AuthShell>
    );
  }

  if (state === "error") {
    return (
      <AuthShell title="We couldn't verify that link" subtitle={message}>
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-3 text-error">
            <XCircle className="h-6 w-6" />
            <span className="font-label-md text-label-md">Link invalid or expired.</span>
          </div>
          <form onSubmit={handleResend} className="flex flex-col gap-3">
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="Enter your email to get a new link"
              className={inputClass}
            />
            <button type="submit" className={primaryBtnClass}>
              <MailCheck className="h-4 w-4" /> Send a new link
            </button>
          </form>
          {resent && <p className="text-on-surface-variant text-body-sm">{message}</p>}
          <Link to="/login" className="text-secondary text-body-sm hover:underline">
            Back to sign in
          </Link>
        </div>
      </AuthShell>
    );
  }

  // idle — arrived here without a token (e.g. straight after registering)
  return (
    <AuthShell
      title="Check your inbox"
      subtitle="We've sent you a link to confirm your email address. It may take a minute to arrive."
    >
      <form onSubmit={handleResend} className="flex flex-col gap-3">
        <input
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Resend to this email"
          className={inputClass}
        />
        <button type="submit" className={primaryBtnClass}>
          <MailCheck className="h-4 w-4" /> Resend verification email
        </button>
      </form>
      {resent && <p className="mt-3 text-on-surface-variant text-body-sm">{message}</p>}
      <Link to="/login" className="mt-4 inline-block text-secondary text-body-sm hover:underline">
        Back to sign in
      </Link>
    </AuthShell>
  );
}
