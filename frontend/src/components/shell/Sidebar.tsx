import { NavLink } from "react-router-dom";
import type { ShellNavItem } from "./navTypes";

/**
 * Desktop-only vertical nav rail. Hidden below `md`; MobileNav covers small
 * screens instead. Purely presentational — the caller supplies `items` so
 * the same component serves every role.
 */
export default function Sidebar({ items, roleLabel }: { items: ShellNavItem[]; roleLabel?: string }) {
  return (
    <aside className="hidden md:flex md:w-60 md:flex-col md:flex-shrink-0 border-r border-border-subtle bg-surface min-h-screen sticky top-0">
      <div className="h-16 flex items-center gap-2 px-5 border-b border-border-subtle">
        <img alt="LifeLink" className="h-7 w-7 object-contain rounded bg-primary/10 p-1" src="/reference/lifelink-logo.png" />
        <span className="font-headline-sm text-headline-sm text-primary">LifeLink</span>
      </div>
      {roleLabel && (
        <div className="px-5 pt-4 pb-1 text-label-sm font-label-sm uppercase tracking-wider text-on-surface-variant">
          {roleLabel}
        </div>
      )}
      <nav className="flex-1 px-3 py-2 space-y-0.5" aria-label="Primary">
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg text-body-sm transition-colors ${
                  isActive
                    ? "bg-primary/10 text-primary font-label-md"
                    : "text-on-surface-variant hover:bg-surface-container-low hover:text-on-surface"
                }`
              }
            >
              <Icon className="h-4.5 w-4.5 flex-shrink-0" />
              <span className="truncate">{item.label}</span>
            </NavLink>
          );
        })}
      </nav>
    </aside>
  );
}
