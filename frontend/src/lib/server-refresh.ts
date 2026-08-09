/**
 * Server-side refresh-token rotation, de-duplicated within this process.
 *
 * Refresh tokens are single-use: the backend rotates (invalidates) the token on
 * every successful `/api/v1/auth/refresh`. A burst of parallel 401s — multiple
 * browser tabs, or parallel `/api/auth/me` calls each minting their own refresh
 * — all POST the same token, so every caller after the first loses the race and
 * gets a 401. The routes treat that 401 as "logged out" and clear the session
 * cookies, logging the user out mid-burst (visible in the backend logs as a
 * wall of `Invalid or expired refresh token`).
 *
 * This module collapses concurrent refreshes sharing the same token into ONE
 * backend round-trip; every waiter receives the same rotated tokens.
 */
import type { NextRequest } from "next/server";

import { backendFetch, BackendApiError } from "@/lib/server-api";

interface RefreshOutcome {
  ok: boolean;
  status: number;
  accessToken?: string;
  refreshToken?: string;
}

let inFlight: { token: string; promise: Promise<RefreshOutcome> } | null = null;

function backendRefresh(refreshToken: string): Promise<RefreshOutcome> {
  return backendFetch<{ access_token: string; refresh_token: string }>(
    "/api/v1/auth/refresh",
    { method: "POST", body: JSON.stringify({ refresh_token: refreshToken }) },
  )
    .then((data) => ({
      ok: true,
      status: 200,
      accessToken: data.access_token,
      refreshToken: data.refresh_token,
    }))
    .catch((error) => ({
      ok: false,
      status: error instanceof BackendApiError ? error.status : 500,
    }));
}

export type RefreshSessionResult =
  | { ok: true; accessToken: string; refreshToken?: string }
  | { ok: false; status: number };

/** Mint a fresh access token from the refresh cookie in `request`. */
export async function refreshSession(request: NextRequest): Promise<RefreshSessionResult> {
  const refreshToken = request.cookies.get("refresh_token")?.value;
  if (!refreshToken) {
    return { ok: false, status: 401 };
  }

  if (!inFlight || inFlight.token !== refreshToken) {
    inFlight = { token: refreshToken, promise: backendRefresh(refreshToken) };
  }

  const outcome = await inFlight.promise;

  // A successful rotation CONSUMES the token (single-use). Cache the result so
  // any concurrent straggler still holding the old token reuses it instead of
  // re-rotating a now-dead token (which would 401 and clear the session). A
  // failed refresh does NOT consume the token, so clear the slot to let the
  // same token be retried.
  if (!outcome.ok && inFlight?.token === refreshToken) {
    inFlight = null;
  }

  if (!outcome.ok || !outcome.accessToken) {
    return { ok: false, status: outcome.status };
  }
  return { ok: true, accessToken: outcome.accessToken, refreshToken: outcome.refreshToken };
}
