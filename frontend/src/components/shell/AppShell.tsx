import { type ReactNode, useState } from "react";
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";
import MobileNav from "./MobileNav";
import type { ShellNavItem } from "./navTypes";

/**
 * Opt-in shared shell: sidebar (desktop) + slide-over nav (mobile) + topbar,
 * wrapping page content. Existing dashboards keep their own bespoke headers
 * (each already ships a working, tested layout) — this is here so *new*
 * role pages don't have to hand-roll navigation again, per the Phase 1
 * "don't duplicate large nav structures" instruction. Adopting it on an
 * existing page is a follow-up, deliberately not done in this pass to avoid
 * an unrelated visual/regression risk across ~20 already-shipped screens.
 */
export default function AppShell({
  title,
  roleLabel,
  navItems,
  actions,
  children,
}: {
  title: string;
  roleLabel?: string;
  navItems: ShellNavItem[];
  actions?: ReactNode;
  children: ReactNode;
}) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="min-h-screen bg-background flex">
      <Sidebar items={navItems} roleLabel={roleLabel} />
      <MobileNav open={mobileOpen} onClose={() => setMobileOpen(false)} items={navItems} />
      <div className="flex-1 min-w-0 flex flex-col">
        <Topbar title={title} onMenuClick={() => setMobileOpen(true)} actions={actions} />
        <main className="flex-1 p-margin-mobile md:p-margin-desktop">{children}</main>
      </div>
    </div>
  );
}
