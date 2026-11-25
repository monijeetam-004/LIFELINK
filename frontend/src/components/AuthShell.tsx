import { type ReactNode } from "react";
import { Link } from "react-router-dom";
import { motion } from "framer-motion";

import ShaderBackground from "./ShaderBackground";

/**
 * Shared split-screen chrome for the secondary auth pages (verify email,
 * forgot / reset password). Mirrors the visual language of AuthPage without
 * duplicating its full role-picker form.
 */
export default function AuthShell({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <div className="min-h-screen w-full flex bg-background overflow-hidden">
      <div className="relative hidden lg:flex lg:w-[42%] flex-col justify-between overflow-hidden bg-[#0c0f14] text-white p-12">
        <div className="absolute inset-0 opacity-60">
          <ShaderBackground className="w-full h-full" />
        </div>
        <div className="absolute inset-0 bg-gradient-to-b from-[#0c0f14]/70 via-[#0c0f14]/60 to-[#0c0f14]/90" />
        <Link to="/" className="relative z-10 flex items-center gap-3">
          <img
            alt="LifeLink"
            className="h-9 w-9 object-contain rounded-lg bg-white/10 p-1"
            src="/reference/lifelink-logo.png"
          />
          <span className="font-headline-md text-headline-md font-bold tracking-tight">
            LifeLink
          </span>
        </Link>
        <div className="relative z-10">
          <h1 className="font-headline-xl text-headline-xl tracking-tight mb-4">
            Every second counts.
          </h1>
          <p className="text-white/70 font-body-md text-body-md max-w-sm">
            Secure access for patients, families, donors, hospitals and admins.
          </p>
        </div>
        <div className="relative z-10 text-white/40 text-body-sm">© LifeLink</div>
      </div>

      <div className="relative flex-1 flex items-center justify-center px-6 py-10 sm:px-10">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: "easeOut" }}
          className="w-full max-w-md"
        >
          <div className="flex items-center gap-3 mb-8 lg:hidden">
            <img alt="LifeLink" className="h-8 w-8 object-contain" src="/reference/lifelink-logo.png" />
            <span className="text-headline-md font-headline-md font-bold text-primary">LifeLink</span>
          </div>
          <h2 className="font-headline-lg text-headline-lg text-on-surface tracking-tight">
            {title}
          </h2>
          {subtitle && (
            <p className="text-on-surface-variant font-body-sm text-body-sm mt-2">{subtitle}</p>
          )}
          <div className="mt-7">{children}</div>
        </motion.div>
      </div>
    </div>
  );
}

export const inputClass =
  "w-full pl-4 pr-4 py-3 rounded-xl border border-outline-variant bg-surface text-on-surface font-body-md text-body-md focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary transition-all";

export const primaryBtnClass =
  "mt-2 w-full bg-primary hover:bg-primary-container text-on-primary font-label-md text-label-md py-3.5 rounded-xl shadow-lg hover:shadow-xl hover:-translate-y-0.5 active:scale-[0.99] transition-all flex items-center justify-center gap-2 disabled:opacity-70 disabled:hover:translate-y-0";
