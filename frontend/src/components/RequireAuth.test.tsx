/**
 * Phase 33 -- component tests for RequireAuth's route-guard presentation
 * contract.
 *
 * IMPORTANT SCOPE NOTE (restated in the Phase 33 report): these tests verify
 * frontend presentation/routing only -- which screen renders for a given
 * (user, roles, loading) combination -- and are NOT proof of backend
 * authorization. UI tests verify presentation of API outcomes; backend tests
 * verify authorization. RequireAuth is explicitly documented (in its own
 * source comment) as "UX only -- the backend is the authority on access,"
 * and nothing here weakens that guard or touches backend RBAC.
 *
 * Rendered the same way App.tsx really uses it -- `<RequireAuth roles={...}>`
 * directly as a route's element -- with a real sibling "/login" route so a
 * redirect is observable as an actual rendered screen, not just an
 * unasserted side effect.
 *
 * Mocking boundary: only `../lib/auth`'s `useAuth` is mocked. AccessDenied
 * and AccountMenu render for real (neither makes a network call).
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import RequireAuth from "./RequireAuth";
import { createTestQueryClient } from "../test/test-utils";
import type { Role } from "../lib/types";

const { authState } = vi.hoisted(() => ({
  authState: {
    user: null as null | { id: string; email: string; full_name: string; role: Role; is_email_verified: boolean; is_active: boolean },
    loading: false,
  },
}));

vi.mock("../lib/auth", () => ({
  useAuth: () => ({
    user: authState.user,
    loading: authState.loading,
    login: vi.fn(),
    register: vi.fn(),
    logout: vi.fn(),
    refresh: vi.fn(),
  }),
}));

function renderProtected(roles?: Role[]) {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/protected"]}>
        <Routes>
          <Route
            path="/protected"
            element={
              <RequireAuth roles={roles}>
                <div>Protected content</div>
              </RequireAuth>
            }
          />
          <Route path="/login" element={<div>Login page</div>} />
          <Route path="/verify-email" element={<div>Verify email page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RequireAuth", () => {
  it("redirects to /login when there is no signed-in user", () => {
    authState.user = null;
    authState.loading = false;
    renderProtected(["PATIENT"]);
    expect(screen.getByText("Login page")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });

  it("renders the protected content when the signed-in user has a permitted role", () => {
    authState.user = { id: "u1", email: "p@example.com", full_name: "Pat Patient", role: "PATIENT", is_email_verified: true, is_active: true };
    authState.loading = false;
    renderProtected(["PATIENT"]);
    expect(screen.getByText("Protected content")).toBeInTheDocument();
    expect(screen.queryByText("Login page")).not.toBeInTheDocument();
  });

  it("renders the existing AccessDenied screen (not a redirect) when the signed-in user's role is not allowed", () => {
    authState.user = { id: "u1", email: "d@example.com", full_name: "Dana Donor", role: "DONOR", is_email_verified: true, is_active: true };
    authState.loading = false;
    renderProtected(["PATIENT"]);
    expect(screen.getByText("Access denied")).toBeInTheDocument();
    expect(screen.getByText("403")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    expect(screen.queryByText("Login page")).not.toBeInTheDocument();
  });

  it("never renders protected content while the auth check is still loading, even for an eventually-permitted role", () => {
    authState.user = { id: "u1", email: "p@example.com", full_name: "Pat Patient", role: "PATIENT", is_email_verified: true, is_active: true };
    authState.loading = true;
    renderProtected(["PATIENT"]);
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    expect(screen.queryByText("Login page")).not.toBeInTheDocument();
    expect(screen.queryByText("Access denied")).not.toBeInTheDocument();
  });

  it("treats a failed/unresolved auth check the same as unauthenticated -- redirects to /login rather than granting access", () => {
    // RequireAuth has exactly one "no user" branch; a failed auth check that
    // resolves to no user takes the same real path as a plain logged-out
    // visit -- there is no separate "auth error" state to fabricate here.
    authState.user = null;
    authState.loading = false;
    renderProtected();
    expect(screen.getByText("Login page")).toBeInTheDocument();
  });

  it("never silently grants access when no role in the allow-list matches, including an empty allow-list", () => {
    authState.user = { id: "u1", email: "d@example.com", full_name: "Dana Donor", role: "DONOR", is_email_verified: true, is_active: true };
    authState.loading = false;
    renderProtected([]);
    expect(screen.getByText("Access denied")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });

  it("renders protected content directly when no roles restriction is given at all", () => {
    authState.user = { id: "u1", email: "d@example.com", full_name: "Dana Donor", role: "DONOR", is_email_verified: true, is_active: true };
    authState.loading = false;
    renderProtected(undefined);
    expect(screen.getByText("Protected content")).toBeInTheDocument();
  });
  it("sends a signed-in but unverified user to /verify-email instead of the dashboard", () => {
    authState.user = { id: "u1", email: "p@example.com", full_name: "Pat Patient", role: "PATIENT", is_email_verified: false, is_active: true };
    authState.loading = false;
    renderProtected(["PATIENT"]);
    expect(screen.getByText("Verify email page")).toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });
});
