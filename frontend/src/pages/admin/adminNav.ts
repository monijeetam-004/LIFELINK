/**
 * Shared Admin Portal sidebar nav — one source of truth so every admin
 * screen's sidebar stays in sync as new sections are added (Phase 6 added
 * Facilities, Donors and the Audit Feed to what was previously five
 * hand-duplicated copies of this same array).
 */
export const ADMIN_NAV = [
  { path: "/admin", icon: "dashboard", label: "Overview" },
  { path: "/admin/verifications", icon: "verified_user", label: "Verifications" },
  { path: "/admin/users", icon: "manage_accounts", label: "Users & Roles" },
  { path: "/admin/facilities", icon: "local_hospital", label: "Facilities" },
  { path: "/admin/donors", icon: "volunteer_activism", label: "Donors" },
  { path: "/admin/transport-routes", icon: "hub", label: "Transport Routes" },
  { path: "/admin/shipments", icon: "local_shipping", label: "Shipments" },
  { path: "/admin/audit-feed", icon: "history_edu", label: "Audit Feed" },
  { path: "/admin/analytics", icon: "insights", label: "Analytics" },
];

export const ACCOUNT_NAV = [
  { path: "/notifications", icon: "notifications", label: "Notifications" },
  { path: "/profile", icon: "account_circle", label: "Profile" },
  { path: "/settings", icon: "tune", label: "Settings" },
  { path: "/help", icon: "help_outline", label: "Help & Support" },
];
