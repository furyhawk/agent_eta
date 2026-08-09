import { NextRequest, NextResponse } from "next/server";
import { clearAuthCookies, setAuthCookies } from "@/lib/auth-cookies";
import { refreshSession } from "@/lib/server-refresh";

export async function POST(request: NextRequest) {
  if (!request.cookies.get("refresh_token")?.value) {
    return NextResponse.json({ detail: "No refresh token" }, { status: 401 });
  }

  const refreshed = await refreshSession(request);

  if (!refreshed.ok) {
    const response = NextResponse.json({ detail: "Session expired" }, { status: refreshed.status });

    clearAuthCookies(response);

    return response;
  }

  const response = NextResponse.json({
    access_token: refreshed.accessToken,
    message: "Token refreshed",
  });

  // The refresh token is only re-set when the backend rotated it.
  setAuthCookies(response, {
    accessToken: refreshed.accessToken,
    refreshToken: refreshed.refreshToken,
  });

  return response;
}
