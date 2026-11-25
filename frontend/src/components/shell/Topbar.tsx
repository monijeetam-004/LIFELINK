import { type ReactNode } from "react";
import { Menu } from "lucide-react";
import NotificationsBell from "../NotificationsBell";

/**
 * Slim top bar: mobile menu trigger, page title, actions slot and the
 * notifications bell. Sign-out/account controls stay in the global
 * <AccountMenu/> (rendered once by RequireAuth) to avoid two logout
 * controls on screen.
 */
export default function Topbar({
  title,
  onMenuClick,
  actions,
}: {
  title: string;
  onMenuClick?: () => void;
  actions?: ReactNode;
}) {
  return (
    <header className="sticky top-0 z-30 h-16 bg-surface/90 backdrop-blur-md border-b border-border-subtle flex items-center justify-between px-margin-mobile md:px-margin-desktop gap-4">
      <div className="flex items-center gap-3 min-w-0">
        {onMenuClick && (
          <button
            type="button"
            onClick={onMenuClick}
            className="md:hidden p-2 -ml-2 rounded-full text-on-surface-variant hover:bg-surface-container-low"
            aria-label="Open navigation menu"
          >
            <Menu className="h-5 w-5" />
          </button>
        )}
        <h1 className="font-headline-md text-headline-md text-on-surface truncate">{title}</h1>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        {actions}
        <NotificationsBell buttonClassName="relative p-2 text-on-surface-variant hover:bg-surface-container rounded-full transition-all" />
      </div>
    </header>
  );
}
