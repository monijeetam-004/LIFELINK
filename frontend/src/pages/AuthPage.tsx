import { useState, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import {
  Mail,
  Lock,
  User,
  Eye,
  EyeOff,
  Droplet,
  HeartPulse,
  Building2,
  Users,
  ArrowRight,
  Loader2,
  Sparkles,
} from "lucide-react";
import ShaderBackground from "../components/ShaderBackground";
import { useAuth } from "../lib/auth";
import { homeForRole } from "../lib/roles";
import { toApiError } from "../lib/api";
import type { Role } from "../lib/types";

type Mode = "login" | "register";

interface RoleOption {
  id: Role;
  label: string;
  desc: string;
  icon: typeof HeartPulse;
}

// Roles a user may self-register as. ADMIN accounts are provisioned by the team.
const ROLES: RoleOption[] = [
  { id: "PATIENT", label: "Patient", desc: "Request blood & track matches", icon: HeartPulse },
  { id: "FAMILY", label: "Family", desc: "Act on behalf of a patient", icon: Users },
  { id: "DONOR", label: "Donor", desc: "Manage availability & donations", icon: Droplet },
  { id: "HOSPITAL", label: "Hospital Staff", desc: "Coordinate inventory & requests", icon: Building2 },
];

const QUOTES = [
  { stat: "12 min", label: "Average time to match a critical request" },
  { stat: "50k+", label: "Verified donors across the network" },
  { stat: "98.4%", label: "Successful match rate last quarter" },
  { stat: "24/7", label: "Emergency coordination with partner hospitals" },
];

/**
 * LifeLink authentication gateway — the app's real entry point.
 * Split-screen: left is the living brand surface (shader + rotating impact
 * stats), right is the login/register card with role selection. Submitting
 * calls the backend auth API; the session is held in an httpOnly cookie.
 */
export default function AuthPage() {
  const navigate = useNavigate();
  const { user, loading, login, register } = useAuth();
  const [mode, setMode] = useState<Mode>("login");
  const [role, setRole] = useState<Role>("PATIENT");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [quoteIndex, setQuoteIndex] = useState(0);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  useEffect(() => {
    const t = setInterval(() => setQuoteIndex((i) => (i + 1) % QUOTES.length), 3800);
    return () => clearInterval(t);
  }, []);

  // Already signed in (e.g. came back to "/" or "/login" with a live
  // session) — send straight to their dashboard instead of showing the
  // auth form again.
  useEffect(() => {
    if (!loading && user && user.is_email_verified) {
      navigate(homeForRole(user.role), { replace: true });
    }
  }, [loading, user, navigate]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      if (mode === "register") {
        await register({ full_name: name, email, password, role });
        navigate("/verify-email", { replace: true });
        return;
      }
      const user = await login(email, password);
      if (!user.is_email_verified) {
        navigate("/verify-email", { replace: true });
        return;
      }
      navigate(homeForRole(user.role), { replace: true });
    } catch (err) {
      setError(toApiError(err).detail);
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen w-full flex bg-background dark:bg-inverse-surface overflow-hidden">
      {/* Left — brand / impact panel */}
      <div className="relative hidden lg:flex lg:w-[46%] xl:w-[42%] flex-col justify-between overflow-hidden bg-[#0c0f14] text-white p-12">
        <div className="absolute inset-0 opacity-60">
          <ShaderBackground className="w-full h-full" />
        </div>
        <div className="absolute inset-0 bg-gradient-to-b from-[#0c0f14]/70 via-[#0c0f14]/60 to-[#0c0f14]/90" />

        <div className="relative z-10 flex items-center gap-3">
          <img alt="LifeLink" className="h-9 w-9 object-contain rounded-lg bg-white/10 p-1" src="/reference/lifelink-logo.png" />
          <span className="font-headline-md text-headline-md font-bold tracking-tight">LifeLink</span>
        </div>

        <div className="relative z-10">
          <div className="inline-flex items-center gap-2 bg-white/10 border border-white/15 rounded-full px-4 py-1.5 mb-8 backdrop-blur-sm">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-red-400"></span>
            </span>
            <span className="text-white/90 font-label-md text-label-md">Real-time Emergency Network Active</span>
          </div>

          <h1 className="font-headline-xl text-headline-xl md:text-[44px] md:leading-[1.12] tracking-tight mb-5">
            Every second counts.
            <br />
            <span className="bg-gradient-to-r from-red-400 to-orange-300 bg-clip-text text-transparent">
              Let's get you where you're needed.
            </span>
          </h1>
          <p className="text-white/70 font-body-md text-body-md max-w-sm mb-10">
            One account, five ways in — patients, families, donors, hospitals and admins each land straight on the tools built for them.
          </p>

          <AnimatePresence mode="wait">
            <motion.div
              key={quoteIndex}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.4 }}
              className="border-l-2 border-red-400/60 pl-5"
            >
              <div className="font-headline-lg text-headline-lg font-bold text-white">{QUOTES[quoteIndex].stat}</div>
              <div className="text-white/60 font-body-sm text-body-sm mt-1">{QUOTES[quoteIndex].label}</div>
            </motion.div>
          </AnimatePresence>
        </div>

        <div className="relative z-10 flex items-center gap-1.5">
          {QUOTES.map((_, i) => (
            <span key={i} className={`h-1 rounded-full transition-all duration-500 ${i === quoteIndex ? "w-6 bg-red-400" : "w-1.5 bg-white/25"}`} />
          ))}
        </div>
      </div>

      {/* Right — auth card */}
      <div className="relative flex-1 flex items-center justify-center px-6 py-10 sm:px-10">
        <div className="absolute inset-0 -z-10 opacity-[0.08] lg:hidden">
          <ShaderBackground className="w-full h-full" />
        </div>

        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, ease: "easeOut" }}
          className="w-full max-w-md"
        >
          <div className="flex items-center gap-3 mb-8 lg:hidden">
            <img alt="LifeLink" className="h-8 w-8 object-contain" src="/reference/lifelink-logo.png" />
            <span className="text-headline-md font-headline-md font-bold text-primary">LifeLink</span>
          </div>

          <div className="mb-7">
            <h2 className="font-headline-lg text-headline-lg text-on-surface dark:text-white tracking-tight">
              {mode === "login" ? "Welcome back" : "Create your account"}
            </h2>
            <p className="text-on-surface-variant dark:text-white/60 font-body-sm text-body-sm mt-2">
              {mode === "login" ? "Sign in to reach your dashboard." : "Join the network — it takes under a minute."}
            </p>
          </div>

          {/* Mode switch */}
          <div className="relative grid grid-cols-2 bg-surface-container-low dark:bg-white/5 rounded-xl p-1 mb-7">
            <motion.div
              className="absolute inset-y-1 w-[calc(50%-4px)] bg-surface dark:bg-white/15 rounded-lg shadow-sm"
              animate={{ x: mode === "login" ? 0 : "calc(100% + 8px)" }}
              transition={{ type: "spring", stiffness: 400, damping: 32 }}
            />
            <button
              type="button"
              onClick={() => {
                setMode("login");
                setError("");
              }}
              className={`relative z-10 py-2.5 rounded-lg font-label-md text-label-md transition-colors ${
                mode === "login" ? "text-primary dark:text-white" : "text-on-surface-variant dark:text-white/50"
              }`}
            >
              Sign in
            </button>
            <button
              type="button"
              onClick={() => {
                setMode("register");
                setError("");
              }}
              className={`relative z-10 py-2.5 rounded-lg font-label-md text-label-md transition-colors ${
                mode === "register" ? "text-primary dark:text-white" : "text-on-surface-variant dark:text-white/50"
              }`}
            >
              Create account
            </button>
          </div>

          {/* Role picker — register only */}
          <AnimatePresence initial={false}>
            {mode === "register" && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.25 }}
                className="overflow-hidden"
              >
                <div className="mb-7">
                  <span className="text-label-sm font-label-sm text-on-surface-variant dark:text-white/50 uppercase tracking-wider">
                    I am a
                  </span>
                  <div className="grid grid-cols-2 gap-3 mt-3">
                    {ROLES.map((r) => {
                      const Icon = r.icon;
                      const active = role === r.id;
                      return (
                        <button
                          type="button"
                          key={r.id}
                          onClick={() => setRole(r.id)}
                          className={`text-left rounded-xl border p-3.5 transition-all ${
                            active
                              ? "border-primary bg-primary/5 dark:bg-white/10 shadow-sm"
                              : "border-outline-variant dark:border-white/10 hover:border-primary/40 hover:bg-surface-container-low dark:hover:bg-white/5"
                          }`}
                        >
                          <Icon className={`h-5 w-5 mb-2 ${active ? "text-primary dark:text-red-300" : "text-on-surface-variant dark:text-white/50"}`} />
                          <div className={`font-label-md text-label-md ${active ? "text-on-surface dark:text-white" : "text-on-surface dark:text-white/80"}`}>
                            {r.label}
                          </div>
                          <div className="text-on-surface-variant dark:text-white/40 text-[11px] leading-tight mt-0.5">{r.desc}</div>
                        </button>
                      );
                    })}
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <AnimatePresence initial={false}>
              {mode === "register" && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.25 }}
                  className="overflow-hidden"
                >
                  <div className="relative">
                    <label htmlFor="auth-name" className="sr-only">
                      Full name
                    </label>
                    <User className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4.5 w-4.5 text-on-surface-variant dark:text-white/40" />
                    <input
                      id="auth-name"
                      type="text"
                      required={mode === "register"}
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="Full name"
                      autoComplete="name"
                      className="w-full pl-11 pr-4 py-3 rounded-xl border border-outline-variant dark:border-white/10 bg-surface dark:bg-white/5 text-on-surface dark:text-white font-body-md text-body-md focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary transition-all"
                    />
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            <div className="relative">
              <label htmlFor="auth-email" className="sr-only">
                Email address
              </label>
              <Mail className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4.5 w-4.5 text-on-surface-variant dark:text-white/40" />
              <input
                id="auth-email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Email address"
                autoComplete="email"
                className="w-full pl-11 pr-4 py-3 rounded-xl border border-outline-variant dark:border-white/10 bg-surface dark:bg-white/5 text-on-surface dark:text-white font-body-md text-body-md focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary transition-all"
              />
            </div>

            <div className="relative">
              <label htmlFor="auth-password" className="sr-only">
                Password
              </label>
              <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4.5 w-4.5 text-on-surface-variant dark:text-white/40" />
              <input
                id="auth-password"
                type={showPassword ? "text" : "password"}
                required
                minLength={mode === "register" ? 8 : undefined}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Password"
                autoComplete={mode === "register" ? "new-password" : "current-password"}
                className="w-full pl-11 pr-11 py-3 rounded-xl border border-outline-variant dark:border-white/10 bg-surface dark:bg-white/5 text-on-surface dark:text-white font-body-md text-body-md focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary transition-all"
              />
              <button
                type="button"
                onClick={() => setShowPassword((s) => !s)}
                aria-label={showPassword ? "Hide password" : "Show password"}
                className="absolute right-3.5 top-1/2 -translate-y-1/2 text-on-surface-variant dark:text-white/40 hover:text-primary transition-colors"
                tabIndex={-1}
              >
                {showPassword ? <EyeOff className="h-4.5 w-4.5" /> : <Eye className="h-4.5 w-4.5" />}
              </button>
            </div>

            {mode === "login" && (
              <div className="flex justify-end -mt-1">
                <Link to="/forgot-password" className="text-secondary text-body-sm font-body-sm hover:underline">
                  Forgot password?
                </Link>
              </div>
            )}

            {error && (
              <p className="text-error text-body-sm font-body-sm -mt-1" role="alert">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="mt-2 w-full bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md py-3.5 rounded-xl shadow-lg hover:shadow-xl hover:-translate-y-0.5 active:scale-[0.99] transition-all flex items-center justify-center gap-2 disabled:opacity-70 disabled:hover:translate-y-0"
            >
              {submitting ? (
                <>
                  <Loader2 className="h-4.5 w-4.5 animate-spin" />
                  {mode === "login" ? "Signing in…" : "Creating account…"}
                </>
              ) : (
                <>
                  {mode === "login" ? "Sign in" : "Create account"}
                  <ArrowRight className="h-4.5 w-4.5" />
                </>
              )}
            </button>
          </form>

          <div className="flex items-center gap-3 my-7">
            <div className="h-px flex-1 bg-outline-variant dark:bg-white/10" />
            <span className="text-on-surface-variant dark:text-white/30 text-[11px] uppercase tracking-wider">or</span>
            <div className="h-px flex-1 bg-outline-variant dark:bg-white/10" />
          </div>

          <button
            type="button"
            onClick={() => navigate("/recommendation")}
            className="w-full flex items-center justify-center gap-2 border-2 border-outline-variant dark:border-white/15 text-on-surface dark:text-white font-label-md text-label-md py-3 rounded-xl hover:bg-surface-container-low dark:hover:bg-white/5 transition-all"
          >
            <Sparkles className="h-4 w-4 text-primary" />
            Explore an emergency search first
          </button>

          <p className="text-center text-on-surface-variant dark:text-white/40 text-body-sm font-body-sm mt-8">
            {mode === "login" ? (
              <>
                New to LifeLink?{" "}
                <button type="button" onClick={() => setMode("register")} className="text-primary font-semibold hover:underline">
                  Create an account
                </button>
              </>
            ) : (
              <>
                Already registered?{" "}
                <button type="button" onClick={() => setMode("login")} className="text-primary font-semibold hover:underline">
                  Sign in
                </button>
              </>
            )}
          </p>
        </motion.div>
      </div>
    </div>
  );
}
