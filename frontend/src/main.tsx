import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { AuthProvider, useAuth } from "./lib/auth";
import { RealtimeProvider } from "./lib/realtime";
import { ToastProvider } from "./lib/toast";
import ErrorBoundary from "./components/ErrorBoundary";
// Hardening: fonts are self-hosted (bundled from npm, OFL/Apache licensed),
// so the app renders identically offline and the CSP allows no font host.
import "@fontsource-variable/inter";
import "@fontsource-variable/plus-jakarta-sans";
import "material-symbols/outlined.css";
import "./index.css";

const queryClient = new QueryClient();

const signalSessionEnded = () =>
  window.dispatchEvent(
    new CustomEvent("lifelink:session-expired", { detail: { from: window.location.pathname } }),
  );

/** Phase F: one live event stream per signed-in user (re-keyed on user
 * change so a different account never inherits the previous stream). */
function RealtimeGate({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  return (
    <RealtimeProvider key={user?.id ?? "anon"} enabled={!!user} onSessionEnded={signalSessionEnded}>
      {children}
    </RealtimeProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <RealtimeGate>
              <ToastProvider>
                <App />
              </ToastProvider>
            </RealtimeGate>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
