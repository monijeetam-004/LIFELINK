import { useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { LogOut, User as UserIcon, ChevronUp } from "lucide-react";

import { useAuth } from "../lib/auth";
import { ROLE_LABEL } from "../lib/roles";

/**
 * Small, unobtrusive account control shown on every authenticated page.
 * Deliberately a fixed overlay so it can be added without editing each
 * dashboard's bespoke header markup (that integration is a later refactor).
 */
export default function AccountMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  // Phase J: Escape closes the menu and returns focus to its toggle (it
  // previously had no keyboard dismissal and no expanded state).
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  if (!user) return null;

  async function handleLogout() {
    setBusy(true);
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="fixed bottom-4 left-4 z-50 print:hidden">
      {open && (
        <div id={panelId} className="mb-2 w-56 rounded-xl border border-outline-variant bg-surface shadow-xl p-3 text-on-surface">
          <div className="px-2 py-1.5">
            <div className="font-label-md text-label-md truncate">{user.full_name}</div>
            <div className="text-on-surface-variant text-[12px] truncate">{user.email}</div>
            <div className="mt-1 inline-block rounded-full bg-primary/10 text-primary text-[11px] px-2 py-0.5">
              {ROLE_LABEL[user.role]}
            </div>
          </div>
          {!user.is_email_verified && (
            <button
              type="button"
              onClick={() => navigate("/verify-email")}
              className="mt-1 w-full text-left px-2 py-2 rounded-lg text-[12px] text-amber-700 hover:bg-amber-50"
            >
              Email not verified — verify now
            </button>
          )}
          <button
            type="button"
            onClick={handleLogout}
            disabled={busy}
            className="mt-1 w-full flex items-center gap-2 px-2 py-2 rounded-lg text-label-md text-error hover:bg-error-container/20 disabled:opacity-60"
          >
            <LogOut className="h-4 w-4" />
            {busy ? "Signing out…" : "Sign out"}
          </button>
        </div>
      )}
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-2 rounded-full border border-outline-variant bg-surface shadow-lg pl-2 pr-3 py-1.5 text-on-surface hover:bg-surface-container-low transition-colors"
      >
        <span className="grid h-7 w-7 place-items-center rounded-full bg-primary/10 text-primary">
          <UserIcon className="h-4 w-4" />
        </span>
        <span className="font-label-md text-label-md max-w-[120px] truncate">
          {user.full_name.split(" ")[0]}
        </span>
        <ChevronUp
          className={`h-4 w-4 text-on-surface-variant transition-transform ${open ? "" : "rotate-180"}`}
        />
      </button>
    </div>
  );
}
