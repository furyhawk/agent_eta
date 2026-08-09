import { NextRequest, NextResponse } from "next/server";
import { clearAuthCookies, setAuthCookies } from "@/lib/auth-cookies";
import { backendFetch, BackendApiError } from "@/lib/server-api";
import { refreshSession } from "@/lib/server-refresh";
import type { User } from "@/types";

function fetchMe(token: string) {
  return backendFetch<User>("/api/v1/auth/me", {
    headers: { Authorization: `Bearer ${token}` },
  });
}

/**
 * Returns the current user AND echoes the access token so the client can use it
 * for WebSocket auth (Sec-WebSocket-Protocol). The access cookie is short-lived
 * (15 min); when it has expired we transparently refresh it using the 7-day
 * refresh cookie so the session — and the chat socket — stay alive across
 * reloads without forcing a re-login.
 */
export async function GET(request: NextRequest) {
  const accessToken = request.cookies.get("access_token")?.value;
  const refreshToken = request.cookies.get("refresh_token")?.value;

  if (accessToken) {
    try {
      const data = await fetchMe(accessToken);
      return NextResponse.json({ ...data, access_token: accessToken });
    } catch (error) {
      if (!(error instanceof BackendApiError) || error.status !== 401) {
        const status = error instanceof BackendApiError ? error.status : 500;
        return NextResponse.json({ detail: "Failed to get user" }, { status });
      }
      // 401 → fall through and try to refresh.
    }
  }

  if (!refreshToken) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }

  try {
    const refreshed = await refreshSession(request);
    if (!refreshed.ok) {
      // Refresh failed → truly logged out. Clear cookies.
      throw new Error("refresh_failed");
    }
    const data = await fetchMe(refreshed.accessToken);
    const response = NextResponse.json({ ...data, access_token: refreshed.accessToken });
    setAuthCookies(response, {
      accessToken: refreshed.accessToken,
      refreshToken: refreshed.refreshToken,
    });
    return response;
  } catch {
    // Refresh failed → truly logged out. Clear cookies.
    const response = NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
    clearAuthCookies(response);
    return response;
  }
}
