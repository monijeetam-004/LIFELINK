import type { LucideIcon } from "lucide-react";

export interface ShellNavItem {
  label: string;
  to: string;
  icon: LucideIcon;
  /** Exact-match highlighting instead of prefix match (use for the role's index route). */
  end?: boolean;
}
