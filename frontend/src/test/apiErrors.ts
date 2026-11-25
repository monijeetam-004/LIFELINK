/**
 * Phase 32 -- builds realistic Axios-error-shaped rejections for mocked API
 * calls, so a component's real (never re-implemented) `toApiError()` call
 * classifies them exactly the way it classifies a genuine failed request.
 *
 * A test never hardcodes what message a given status "should" produce --
 * it imports the real `toApiError` from `lib/api` and computes the expected
 * text from the same error object it hands the mock, so this file and the
 * component under test are both exercising the one real mapping table in
 * `lib/api.ts` (see STATUS_FALLBACK_MESSAGE), never a second copy of it.
 */
import type { AxiosError } from "axios";

export function makeAxiosError(
  status: number | undefined,
  detail?: string,
  opts: { code?: string; headers?: Record<string, string> } = {},
): AxiosError {
  const isNetwork = opts.code === "ERR_NETWORK";
  const isTimeout = opts.code === "ECONNABORTED";
  return {
    isAxiosError: true,
    name: "AxiosError",
    message: isNetwork
      ? "Network Error"
      : isTimeout
        ? "timeout of 20000ms exceeded"
        : `Request failed with status code ${status}`,
    code: opts.code,
    response:
      status != null
        ? {
            status,
            data: detail ? { detail } : {},
            headers: opts.headers ?? {},
            statusText: "",
            config: {},
          }
        : undefined,
    config: {
      headers: { get: () => undefined },
    },
    toJSON: () => ({}),
    // `toApiError` only ever reads `isAxiosError`, `code`, `message`,
    // `response.{status,data,headers}` and `config.headers.get(...)` -- this
    // double intentionally implements exactly that subset, not the full
    // (much larger) real AxiosError/InternalAxiosRequestConfig shape, hence
    // the through-`unknown` cast rather than a structurally-complete object.
  } as unknown as AxiosError;
}

/** A genuine 404 -- "no row with this code/id exists". */
export const notFoundError = () => makeAxiosError(404, "That couldn't be found — it may have been removed.");

/** A genuine backend/server failure -- distinct from "not found". */
export const serverError = () => makeAxiosError(500);

/** A network-layer failure -- the request never reached (or returned from) the server. */
export const networkError = () => makeAxiosError(undefined, undefined, { code: "ERR_NETWORK" });

/** A 403 -- authenticated, but not authorized for this action. */
export const forbiddenError = () => makeAxiosError(403);

/** A 401 -- not authenticated (distinct from 403, which is "authenticated but not permitted"). */
export const unauthorizedError = () => makeAxiosError(401);

/** Phase 33 -- a genuine 409: an idempotency-key/state conflict the backend actually rejected. */
export const conflictError = (detail?: string) => makeAxiosError(409, detail);

/** Phase 33 -- a genuine 429, optionally carrying a real `Retry-After` header,
 * exactly as `toApiError`'s real rate-limit branch reads it. */
export const rateLimitError = (retryAfterSeconds?: number) =>
  makeAxiosError(429, undefined, {
    headers: retryAfterSeconds != null ? { "retry-after": String(retryAfterSeconds) } : {},
  });

/** Phase 33 -- a genuine 422 (a real Pydantic-shaped validation rejection, not a client-invented one). */
export const validationError = (detail = "Some of the information provided isn't valid.") =>
  makeAxiosError(422, detail);

/** Phase 33 -- a genuine 503 (backend dependency temporarily unavailable). */
export const serviceUnavailableError = () => makeAxiosError(503);
