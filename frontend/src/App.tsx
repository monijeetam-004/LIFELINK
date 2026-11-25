import { lazy, Suspense } from "react";
import { Routes, Route, Link, Navigate } from "react-router-dom";

import RequireAuth from "./components/RequireAuth";
import OfflineBanner from "./components/OfflineBanner";
import SessionExpiredListener from "./components/SessionExpiredListener";
// Phase 24: the five role landing dashboards stay eager (not lazy) -- see
// the long comment below the lazy block for why, backed by a real
// Playwright regression this phase actually hit and fixed.
import PatientDashboard from "./pages/PatientDashboard";
import HospitalDashboard from "./pages/HospitalDashboard";
import DonorDashboard from "./pages/DonorDashboard";
import FamilyDashboard from "./pages/family/FamilyDashboard";
import AdminOverview from "./pages/admin/AdminOverview";

// Phase 24: every OTHER routed page is loaded on demand (route-level code
// splitting) instead of eagerly at app startup. Before this change,
// `npx vite build` produced one 1.31MB / 312.59kB-gzip JS chunk containing
// all 52 page components regardless of which single route a visitor
// actually landed on; after, the initial chunk is 446.78kB / 144.90kB-gzip
// with the rest split into ~70 on-demand route chunks -- see
// LIFELINK_PHASE_24_PERFORMANCE_AUDIT.md's frontend/bundle section for the
// full measured before/after. `RequireAuth`, `OfflineBanner` and
// `SessionExpiredListener` stay eager too: they are layout chrome mounted
// on (almost) every route, not a page body, so splitting them out would
// just add a second network round-trip for something needed immediately
// on first paint. The existing top-level `<ErrorBoundary>` (mounted once
// in main.tsx, around all of <App/>) is what catches a lazy-chunk load
// failure -- `React.lazy` throws during render exactly like any other
// render error, so no second error-boundary layer is needed here to
// preserve that existing guarantee.
//
// PatientDashboard/HospitalDashboard/DonorDashboard/FamilyDashboard/
// AdminOverview are deliberately EXCLUDED from lazy-loading (imported
// above instead): `helpers.ts::loginAsUi`'s `page.waitForURL(/\/(patient|
// family|donor|hospital|admin)$/)` only waits for the client-side route
// match, not for the destination component to finish mounting, and every
// single login lands on exactly one of these five routes -- there is no
// "maybe visited" case for any of them the way there is for e.g. Settings
// or a specific admin sub-page. Lazy-loading them was tried first and
// caused a real, reproduced regression: e2e/blood_request_idempotency.spec.ts
// reads a "before" row count immediately after the post-login redirect,
// and with these five lazy, that read could land during the brief window
// where the Suspense fallback (not the dashboard's own "Loading your
// requests..." text) was showing -- the dashboard component hadn't
// mounted yet at all, so its own loading text was trivially "not visible"
// for the wrong reason, and `tbody tr` count was read as 0 instead of the
// real accumulated count. Keeping these five eager removes the race
// entirely (they are always loaded anyway, every session needs exactly
// one of them within the first navigation) while every less-than-100%
// route stays lazily loaded for the real bundle-size win above.
const AuthPage = lazy(() => import("./pages/AuthPage"));
const VerifyEmail = lazy(() => import("./pages/VerifyEmail"));
const ForgotPassword = lazy(() => import("./pages/ForgotPassword"));
const ResetPassword = lazy(() => import("./pages/ResetPassword"));
const DonorDashboardMobile = lazy(() => import("./pages/DonorDashboardMobile"));
const DecisionTransparency = lazy(() => import("./pages/DecisionTransparency"));
const MathematicalModel = lazy(() => import("./pages/MathematicalModel"));
const GraphDecisionEngine = lazy(() => import("./pages/GraphDecisionEngine"));
const SimulationMode = lazy(() => import("./pages/SimulationMode"));
const Analytics = lazy(() => import("./pages/Analytics"));
const ResearchMode = lazy(() => import("./pages/ResearchMode"));
const DonationCertificate = lazy(() => import("./pages/DonationCertificate"));
const VerifyCertificate = lazy(() => import("./pages/VerifyCertificate"));
const DonorRecommendation = lazy(() => import("./pages/DonorRecommendation"));
const NotFound = lazy(() => import("./pages/NotFound"));
const FamilyLinkedPatients = lazy(() => import("./pages/family/FamilyLinkedPatients"));
const FamilyPatientDetail = lazy(() => import("./pages/family/FamilyPatientDetail"));
const Profile = lazy(() => import("./pages/shared/Profile"));
const Settings = lazy(() => import("./pages/shared/Settings"));
const NotificationsCenter = lazy(() => import("./pages/shared/NotificationsCenter"));
const HelpAndLegal = lazy(() => import("./pages/shared/HelpAndLegal"));
const StrategyLab = lazy(() => import("./pages/shared/StrategyLab"));
const FindDonor = lazy(() => import("./pages/patient/FindDonor"));
const EmergencySosTracking = lazy(() => import("./pages/patient/EmergencySosTracking"));
const NewRequestGuidedFlow = lazy(() => import("./pages/patient/NewRequestGuidedFlow"));
const RequestHistory = lazy(() => import("./pages/patient/RequestHistory"));
const EligibilityCenter = lazy(() => import("./pages/donor/EligibilityCenter"));
const ReadyConsole = lazy(() => import("./pages/donor/ReadyConsole"));
const MilestonesHistory = lazy(() => import("./pages/donor/MilestonesHistory"));
const IdentityVerification = lazy(() => import("./pages/donor/IdentityVerification"));
const InventoryConsole = lazy(() => import("./pages/hospital/InventoryConsole"));
const DonorDirectoryAndLogistics = lazy(() => import("./pages/hospital/DonorDirectoryAndLogistics"));
const RequestManagementDetail = lazy(() => import("./pages/hospital/RequestManagementDetail"));
const AnalyticsMobile = lazy(() => import("./pages/hospital/AnalyticsMobile"));
const ResourcePathways = lazy(() => import("./pages/hospital/ResourcePathways"));
const UnitLifecycle = lazy(() => import("./pages/hospital/UnitLifecycle"));
const Shipments = lazy(() => import("./pages/hospital/Shipments"));
const FacilityVerification = lazy(() => import("./pages/hospital/FacilityVerification"));
const DonationReviews = lazy(() => import("./pages/hospital/DonationReviews"));
const VerificationsQueue = lazy(() => import("./pages/admin/VerificationsQueue"));
const UserManagement = lazy(() => import("./pages/admin/UserManagement"));
const AdminAnalytics = lazy(() => import("./pages/admin/AdminAnalytics"));
const TransportRoutes = lazy(() => import("./pages/admin/TransportRoutes"));
const AdminShipments = lazy(() => import("./pages/admin/AdminShipments"));
const AdminFacilities = lazy(() => import("./pages/admin/AdminFacilities"));
const AdminDonors = lazy(() => import("./pages/admin/AdminDonors"));
const AdminAuditFeed = lazy(() => import("./pages/admin/AdminAuditFeed"));
const AccessDenied = lazy(() => import("./pages/errors/AccessDenied"));
const SessionExpired = lazy(() => import("./pages/errors/SessionExpired"));
const ServerError = lazy(() => import("./pages/errors/ServerError"));
const OfflinePage = lazy(() => import("./pages/errors/OfflinePage"));

/** Minimal, unobtrusive Suspense fallback shown only for the brief window
 * while a route's own chunk downloads (typically only ever visible on a
 * slow connection's first visit to a given route -- every chunk is cached
 * by the browser after that). Deliberately plain: this is route-transition
 * chrome, not a page's own loading state, so it must not fight with any
 * page's existing internal loading/skeleton UI. */
function RouteLoadingFallback() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <span className="text-on-surface-variant font-body-md" role="status" aria-live="polite">
        Loading…
      </span>
    </div>
  );
}

const NAV = [
  { path: "/recommendation", label: "Donor Recommendation (Home)", group: "Core" },
  { path: "/patient", label: "Patient Dashboard", group: "Dashboards" },
  { path: "/patient/find-donor", label: "Patient — Find Donor / Emergency Search", group: "Dashboards" },
  { path: "/patient/sos-tracking", label: "Patient — Emergency SOS & Live Tracking", group: "Dashboards" },
  { path: "/patient/new-request", label: "Patient — New Request (Guided Flow)", group: "Dashboards" },
  { path: "/patient/history", label: "Patient — Request History", group: "Dashboards" },
  { path: "/family", label: "Family Dashboard", group: "Dashboards" },
  { path: "/family/patients", label: "Family — Linked Patients", group: "Dashboards" },
  { path: "/donor", label: "Donor Dashboard", group: "Dashboards" },
  { path: "/donor/mobile", label: "Donor Dashboard (mobile)", group: "Dashboards" },
  { path: "/donor/eligibility", label: "Donor — Eligibility Center", group: "Dashboards" },
  { path: "/donor/ready-console", label: "Donor — Ready Console", group: "Dashboards" },
  { path: "/donor/milestones", label: "Donor — Milestones, History & Certificates", group: "Dashboards" },
  { path: "/donor/identity-verification", label: "Donor — Identity Verification", group: "Dashboards" },
  { path: "/hospital", label: "Hospital Dashboard", group: "Dashboards" },
  { path: "/hospital/inventory", label: "Hospital — Inventory Console", group: "Dashboards" },
  { path: "/hospital/donors", label: "Hospital — Donor Directory & Logistics", group: "Dashboards" },
  { path: "/hospital/analytics-mobile", label: "Hospital — Analytics (mobile)", group: "Dashboards" },
  { path: "/hospital/resource-pathways", label: "Hospital — Resource Pathways (graph engine)", group: "Dashboards" },
  { path: "/hospital/shipments", label: "Hospital — Shipments & Chain of Custody", group: "Dashboards" },
  { path: "/hospital/verification", label: "Hospital — Facility Verification", group: "Dashboards" },
  { path: "/hospital/donation-reviews", label: "Hospital — Donation Verification", group: "Dashboards" },
  { path: "/admin", label: "Admin Portal", group: "Dashboards" },
  { path: "/admin/verifications", label: "Admin — Verifications Queue", group: "Dashboards" },
  { path: "/admin/users", label: "Admin — User Management", group: "Dashboards" },
  { path: "/admin/facilities", label: "Admin — Facilities", group: "Dashboards" },
  { path: "/admin/donors", label: "Admin — Donor Governance", group: "Dashboards" },
  { path: "/admin/transport-routes", label: "Admin — Transport Routes & Graph", group: "Dashboards" },
  { path: "/admin/shipments", label: "Admin — Shipments & Chain of Custody", group: "Dashboards" },
  { path: "/admin/audit-feed", label: "Admin — Audit Feed", group: "Dashboards" },
  { path: "/admin/analytics", label: "Admin — Analytics & Certificates Registry", group: "Dashboards" },
  { path: "/profile", label: "Profile", group: "Shared" },
  { path: "/settings", label: "Settings", group: "Shared" },
  { path: "/notifications", label: "Notifications", group: "Shared" },
  { path: "/help", label: "Help & Support", group: "Shared" },
  { path: "/legal", label: "Legal & Privacy", group: "Shared" },
  { path: "/decision-transparency", label: "Decision Transparency — Why Not Others", group: "Decision Support" },
  { path: "/mathematical-model", label: "Mathematical Model", group: "Decision Support" },
  { path: "/graph-engine", label: "Graph Decision Engine", group: "Decision Support" },
  { path: "/simulation", label: "Emergency Simulation Mode", group: "Decision Support" },
  { path: "/research-mode", label: "Research Mode", group: "Decision Support" },
  { path: "/matching/strategy-lab", label: "Strategy Lab (real matching engine)", group: "Decision Support" },
  { path: "/analytics", label: "Analytics Command Center", group: "Ops" },
  { path: "/certificate", label: "Donation Certificate", group: "Ops" },
];

/**
 * Dev-only screen index — every ported route in one place. Not linked from
 * the app itself; kept reachable at /dev-index for development/QA.
 */
function DevIndex() {
  const groups = Array.from(new Set(NAV.map((n) => n.group)));
  return (
    <div className="min-h-screen bg-background p-margin-desktop font-body-md">
      <h1 className="font-headline-lg text-headline-lg text-primary mb-2">LifeLink — Screen Index</h1>
      <p className="text-on-surface-variant mb-8">
        Dev-only navigator for the screens ported from the Stitch export. Replace with the real Landing page.
      </p>
      {groups.map((g) => (
        <div key={g} className="mb-8">
          <h2 className="font-headline-md text-lg mb-3 text-on-surface">{g}</h2>
          <div className="flex flex-col gap-2">
            {NAV.filter((n) => n.group === g).map((n) => (
              <Link key={n.path} to={n.path} className="text-secondary hover:underline">
                {n.label} <span className="text-on-surface-variant text-sm">({n.path})</span>
              </Link>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function App() {
  return (
    <>
      <OfflineBanner />
      <SessionExpiredListener />
      <Suspense fallback={<RouteLoadingFallback />}>
      <Routes>
      {/* Public — auth gateway */}
      <Route path="/" element={<AuthPage />} />
      <Route path="/login" element={<AuthPage />} />
      <Route path="/register" element={<AuthPage />} />
      <Route path="/verify-email" element={<VerifyEmail />} />
      <Route path="/verify/:code" element={<VerifyCertificate />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      {/* QA fix: the dev screen index was reachable (anonymously) in
          production builds, publishing the full route map. Dev server only. */}
      {import.meta.env.DEV && <Route path="/dev-index" element={<DevIndex />} />}

      {/* Global error/status pages — directly reachable and used by
          RequireAuth / the api client's error interceptor. */}
      <Route path="/403" element={<AccessDenied />} />
      <Route path="/session-expired" element={<SessionExpired />} />
      <Route path="/500" element={<ServerError />} />
      <Route path="/offline" element={<OfflinePage />} />

      {/* Public — explanatory / research surfaces (kept as-is from the Stitch port) */}
      <Route path="/recommendation" element={<DonorRecommendation />} />
      <Route path="/decision-transparency" element={<DecisionTransparency />} />
      <Route path="/mathematical-model" element={<MathematicalModel />} />
      <Route path="/graph-engine" element={<GraphDecisionEngine />} />
      <Route path="/simulation" element={<SimulationMode />} />
      <Route path="/research-mode" element={<ResearchMode />} />

      {/* Public — help/legal reads before sign-in too */}
      <Route path="/help" element={<HelpAndLegal />} />
      <Route path="/legal" element={<HelpAndLegal />} />
      <Route path="/legal/:section" element={<HelpAndLegal />} />

      {/* Guarded — role dashboards (backend authorization is authoritative) */}
      <Route
        path="/patient"
        element={
          <RequireAuth roles={["PATIENT"]}>
            <PatientDashboard />
          </RequireAuth>
        }
      />
      <Route
        path="/patient/find-donor"
        element={
          <RequireAuth roles={["PATIENT"]}>
            <FindDonor />
          </RequireAuth>
        }
      />
      <Route
        path="/patient/sos-tracking"
        element={
          <RequireAuth roles={["PATIENT"]}>
            <EmergencySosTracking />
          </RequireAuth>
        }
      />
      <Route
        path="/patient/new-request"
        element={
          <RequireAuth roles={["PATIENT"]}>
            <NewRequestGuidedFlow />
          </RequireAuth>
        }
      />
      <Route
        path="/patient/history"
        element={
          <RequireAuth roles={["PATIENT"]}>
            <RequestHistory />
          </RequireAuth>
        }
      />
      <Route
        path="/family"
        element={
          <RequireAuth roles={["FAMILY"]}>
            <FamilyDashboard />
          </RequireAuth>
        }
      />
      <Route
        path="/family/patients"
        element={
          <RequireAuth roles={["FAMILY"]}>
            <FamilyLinkedPatients />
          </RequireAuth>
        }
      />
      <Route
        path="/family/patients/:patientId"
        element={
          <RequireAuth roles={["FAMILY"]}>
            <FamilyPatientDetail />
          </RequireAuth>
        }
      />
      <Route
        path="/donor"
        element={
          <RequireAuth roles={["DONOR"]}>
            <DonorDashboard />
          </RequireAuth>
        }
      />
      <Route
        path="/donor/mobile"
        element={
          <RequireAuth roles={["DONOR"]}>
            <DonorDashboardMobile />
          </RequireAuth>
        }
      />
      <Route
        path="/donor/eligibility"
        element={
          <RequireAuth roles={["DONOR"]}>
            <EligibilityCenter />
          </RequireAuth>
        }
      />
      <Route
        path="/donor/ready-console"
        element={
          <RequireAuth roles={["DONOR"]}>
            <ReadyConsole />
          </RequireAuth>
        }
      />
      <Route
        path="/donor/milestones"
        element={
          <RequireAuth roles={["DONOR"]}>
            <MilestonesHistory />
          </RequireAuth>
        }
      />
      <Route
        path="/donor/identity-verification"
        element={
          <RequireAuth roles={["DONOR"]}>
            <IdentityVerification />
          </RequireAuth>
        }
      />
      <Route
        path="/hospital"
        element={
          <RequireAuth roles={["HOSPITAL"]}>
            <HospitalDashboard />
          </RequireAuth>
        }
      />
      <Route
        path="/hospital/inventory"
        element={
          <RequireAuth roles={["HOSPITAL"]}>
            <InventoryConsole />
          </RequireAuth>
        }
      />
      <Route
        path="/hospital/donors"
        element={
          <RequireAuth roles={["HOSPITAL"]}>
            <DonorDirectoryAndLogistics />
          </RequireAuth>
        }
      />
      <Route
        path="/hospital/requests/:requestId"
        element={
          <RequireAuth roles={["HOSPITAL"]}>
            <RequestManagementDetail />
          </RequireAuth>
        }
      />
      <Route
        path="/hospital/analytics-mobile"
        element={
          <RequireAuth roles={["HOSPITAL"]}>
            <AnalyticsMobile />
          </RequireAuth>
        }
      />
      <Route
        path="/hospital/units"
        element={
          <RequireAuth roles={["HOSPITAL"]}>
            <UnitLifecycle />
          </RequireAuth>
        }
      />
      <Route
        path="/hospital/resource-pathways"
        element={
          <RequireAuth roles={["HOSPITAL"]}>
            <ResourcePathways />
          </RequireAuth>
        }
      />
      <Route
        path="/hospital/shipments"
        element={
          <RequireAuth roles={["HOSPITAL"]}>
            <Shipments />
          </RequireAuth>
        }
      />
      <Route
        path="/hospital/verification"
        element={
          <RequireAuth roles={["HOSPITAL"]}>
            <FacilityVerification />
          </RequireAuth>
        }
      />
      <Route
        path="/hospital/donation-reviews"
        element={
          <RequireAuth roles={["HOSPITAL"]}>
            <DonationReviews />
          </RequireAuth>
        }
      />
      <Route
        path="/admin"
        element={
          <RequireAuth roles={["ADMIN"]}>
            <AdminOverview />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/verifications"
        element={
          <RequireAuth roles={["ADMIN"]}>
            <VerificationsQueue />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/users"
        element={
          <RequireAuth roles={["ADMIN"]}>
            <UserManagement />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/analytics"
        element={
          <RequireAuth roles={["ADMIN"]}>
            <AdminAnalytics />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/transport-routes"
        element={
          <RequireAuth roles={["ADMIN"]}>
            <TransportRoutes />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/shipments"
        element={
          <RequireAuth roles={["ADMIN"]}>
            <AdminShipments />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/facilities"
        element={
          <RequireAuth roles={["ADMIN"]}>
            <AdminFacilities />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/donors"
        element={
          <RequireAuth roles={["ADMIN"]}>
            <AdminDonors />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/audit-feed"
        element={
          <RequireAuth roles={["ADMIN"]}>
            <AdminAuditFeed />
          </RequireAuth>
        }
      />
      {/* Legacy path kept working — redirects into the new /admin base route. */}
      <Route path="/admin/verification" element={<Navigate to="/admin/verifications" replace />} />
      <Route
        path="/analytics"
        element={
          <RequireAuth roles={["ADMIN", "HOSPITAL"]}>
            <Analytics />
          </RequireAuth>
        }
      />
      <Route
        path="/certificate"
        element={
          <RequireAuth>
            <DonationCertificate />
          </RequireAuth>
        }
      />
      <Route
        path="/certificate/:id"
        element={
          <RequireAuth>
            <DonationCertificate />
          </RequireAuth>
        }
      />

      {/* Guarded — shared across every role that can see match-candidates
          for a request/emergency (backend authorization re-checks this
          per item, same as /blood-requests/:id/match-candidates). */}
      <Route
        path="/matching/strategy-lab"
        element={
          <RequireAuth roles={["PATIENT", "FAMILY", "HOSPITAL", "ADMIN"]}>
            <StrategyLab />
          </RequireAuth>
        }
      />

      {/* Guarded — shared across every role */}
      <Route
        path="/profile"
        element={
          <RequireAuth>
            <Profile />
          </RequireAuth>
        }
      />
      <Route
        path="/settings"
        element={
          <RequireAuth>
            <Settings />
          </RequireAuth>
        }
      />
      <Route
        path="/notifications"
        element={
          <RequireAuth>
            <NotificationsCenter />
          </RequireAuth>
        }
      />

      <Route path="*" element={<NotFound />} />
      </Routes>
      </Suspense>
    </>
  );
}
