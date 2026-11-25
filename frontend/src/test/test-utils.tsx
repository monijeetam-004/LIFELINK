/**
 * Phase 32 -- shared component-test rendering helper.
 *
 * Every real page/component in this app expects to run inside a
 * QueryClientProvider (@tanstack/react-query) and, for anything using
 * react-router hooks (useParams/useNavigate/useLocation/<Link>), a Router.
 * This wraps a component under test the same way `App.tsx` really does,
 * without recreating App.tsx's own routing table -- callers pass the one
 * route (`path`) and starting location (`route`) each test actually needs.
 */
import { type ReactElement, type ReactNode } from "react";
import { render, type RenderOptions } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";

/** No retries -- a mocked rejection must surface as an error state on the
 * FIRST attempt, not after react-query's real retry/backoff delay. */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

export interface RenderWithProvidersOptions
  extends Omit<RenderOptions, "wrapper"> {
  /** Starting location, e.g. "/verify/ABC123". Defaults to "/". */
  route?: string;
  /** Route pattern to register the element under, e.g. "/verify/:code" --
   * only needed when the component reads useParams(). */
  path?: string;
  queryClient?: QueryClient;
}

export function renderWithProviders(
  ui: ReactElement,
  { route = "/", path, queryClient = createTestQueryClient(), ...options }: RenderWithProvidersOptions = {},
) {
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>
          {path ? (
            <Routes>
              <Route path={path} element={children} />
            </Routes>
          ) : (
            children
          )}
        </MemoryRouter>
      </QueryClientProvider>
    );
  }

  return { queryClient, ...render(ui, { wrapper: Wrapper, ...options }) };
}

export * from "@testing-library/react";
