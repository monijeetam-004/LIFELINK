import axios, { AxiosError, type AxiosInstance } from "axios";

/**
 * Centralized API client. Every LifeLink request goes through here.
 *
 * - Base URL comes from `VITE_API_URL` (see `.env.example`).
 * - `withCredentials` so the httpOnly session cookie is sent/received.
 * - `X-Requested-With` marks requests as XHR (part of the CSRF defense).
 * - `timeout` bounds every request so a hung connection surfaces as an
 *   honest "timed out" error instead of an infinite spinner.
 * - `X-Request-Id` is attached per-request (also logged with any console
 *   error) so a report of "it broke" can be correlated with backend logs.
 */
export const API_BASE_URL: string =
  import.meta.env.VITE_API_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

const REQUEST_TIMEOUT_MS = 20_000;

export const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  timeout: REQUEST_TIMEOUT_MS,
  headers: { "X-Requested-With": "XMLHttpRequest" },
});

api.interceptors.request.use((config) => {
  config.headers.set?.("X-Request-Id", crypto.randomUUID());
  return config;
});

/** Endpoints where a 401 is an expected, normal outcome — never a "session
 * expired" event (the initial /auth/me probe on load, and bad-credential
 * responses from login/register itself). */
const SESSION_CHECK_PATHS = ["/auth/me", "/auth/session", "/auth/login", "/auth/register", "/auth/logout"];

api.interceptors.response.use(
  (res) => res,
  (error: AxiosError) => {
    const status = error.response?.status;
    const url = error.config?.url ?? "";
    const isSessionCheckPath = SESSION_CHECK_PATHS.some((p) => url.includes(p));

    // A 401 on any *other* authenticated call means a previously-valid
    // session died server-side (expired/revoked) mid-use. Tell the rest of
    // the app so it can show an honest "session expired" state instead of
    // a confusing silent failure on whatever the user was doing.
    if (status === 401 && !isSessionCheckPath) {
      window.dispatchEvent(
        new CustomEvent("lifelink:session-expired", {
          detail: { from: window.location.pathname },
        }),
      );
    }

    // Never swallow — every caller still gets the rejection to handle
    // (loading/error state, a toast, a form error) via toApiError below.
    return Promise.reject(error);
  },
);

export interface ApiError {
  detail: string;
  code?: string;
  status?: number;
  requestId?: string;
  retryAfterSeconds?: number;
}

function extractDetail(data: unknown): string | null {
  if (!data || typeof data !== "object") return null;
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  // FastAPI/Pydantic validation errors (422): detail is an array of
  // { loc, msg, type } objects — join into one readable message.
  if (Array.isArray(detail)) {
    const messages = detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : null))
      .filter((m): m is string => !!m);
    if (messages.length > 0) return messages.join(" ");
  }
  return null;
}

const STATUS_FALLBACK_MESSAGE: Record<number, string> = {
  400: "That request couldn't be processed. Check the details and try again.",
  401: "You need to sign in to do that.",
  403: "You don't have permission to do that.",
  404: "That couldn't be found — it may have been removed.",
  409: "That conflicts with the current state — refresh and try again.",
  422: "Some of the information provided isn't valid.",
  429: "Too many requests — please slow down and try again shortly.",
  500: "Something went wrong on our end. Please try again.",
  502: "The server is temporarily unavailable. Please try again shortly.",
  503: "The server is temporarily unavailable. Please try again shortly.",
};

/** Normalize any thrown value into a predictable `ApiError`. Never returns
 * a blank/empty message — every caller gets something displayable. */
export function toApiError(err: unknown): ApiError {
  // Already normalized (e.g. re-thrown from the auth layer) — pass through.
  if (
    err &&
    typeof err === "object" &&
    "detail" in err &&
    typeof (err as ApiError).detail === "string"
  ) {
    return err as ApiError;
  }

  const axiosErr = err as AxiosError<{ detail?: unknown; code?: string }>;
  if (axiosErr?.isAxiosError) {
    const status = axiosErr.response?.status;
    const requestId = axiosErr.config?.headers?.get?.("X-Request-Id") as string | undefined;

    if (axiosErr.code === "ECONNABORTED") {
      return { detail: "That took too long to respond. Please try again.", code: "TIMEOUT", requestId };
    }
    if (axiosErr.code === "ERR_NETWORK") {
      return {
        detail: "Can't reach the server. Check your connection and try again.",
        code: "NETWORK",
        requestId,
      };
    }

    const detailFromBody = extractDetail(axiosErr.response?.data);
    const code = axiosErr.response?.data?.code;

    if (status === 429) {
      const retryAfterHeader = axiosErr.response?.headers?.["retry-after"];
      const retryAfterSeconds = retryAfterHeader ? Number(retryAfterHeader) : undefined;
      return {
        detail: detailFromBody ?? STATUS_FALLBACK_MESSAGE[429],
        code,
        status,
        requestId,
        retryAfterSeconds: Number.isFinite(retryAfterSeconds) ? retryAfterSeconds : undefined,
      };
    }

    if (detailFromBody) {
      return { detail: detailFromBody, code, status, requestId };
    }
    if (status && STATUS_FALLBACK_MESSAGE[status]) {
      return { detail: STATUS_FALLBACK_MESSAGE[status], code, status, requestId };
    }
    return {
      detail: axiosErr.message || "Something went wrong.",
      status,
      requestId,
    };
  }
  if (err instanceof Error) return { detail: err.message };
  return { detail: "Something went wrong." };
}
