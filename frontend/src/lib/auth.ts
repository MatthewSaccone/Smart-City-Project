import "server-only";
import { cookies } from "next/headers";

export const issuerBase = process.env.AUTHENTIK_PUBLIC_URL || "http://localhost:8088";
export const clientId = process.env.AUTHENTIK_CLIENT_ID || "smart-city-api";
export const frontendUrl = process.env.FRONTEND_PUBLIC_URL || "http://localhost:3000";
export const redirectUri = `${frontendUrl}/api/auth/callback`;
export const cookieOptions = { httpOnly: true, sameSite: "lax" as const, secure: frontendUrl.startsWith("https:"), path: "/" };

export async function exchange(data: Record<string, string>) {
  const response = await fetch(`${issuerBase}/application/o/token/`, {
    method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ ...data, client_id: clientId }), cache: "no-store", redirect: "error",
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) throw new Error("Authentication failed");
  const result = await response.json();
  if (typeof result.access_token !== "string" || result.token_type?.toLowerCase() !== "bearer" || !Number.isFinite(result.expires_in) || result.expires_in <= 0) throw new Error("Invalid token response");
  return result as { access_token: string; expires_in: number; refresh_token?: string };
}

export async function saveTokens(tokens: Awaited<ReturnType<typeof exchange>>) {
  const jar = await cookies();
  jar.set("sc_access", tokens.access_token, { ...cookieOptions, maxAge: Math.max(1, tokens.expires_in - 15) });
  if (typeof tokens.refresh_token === "string") jar.set("sc_refresh", tokens.refresh_token, { ...cookieOptions, maxAge: 3600 });
}

export async function clearTokens() {
  const jar = await cookies();
  for (const key of ["sc_access", "sc_refresh", "sc_oauth_state", "sc_oauth_verifier"]) jar.set(key, "", { ...cookieOptions, maxAge: 0 });
}

export async function accessToken() {
  const jar = await cookies();
  const token = jar.get("sc_access")?.value;
  if (token) return token;
  const refresh = jar.get("sc_refresh")?.value;
  if (!refresh) return null;
  try {
    const result = await exchange({ grant_type: "refresh_token", refresh_token: refresh });
    await saveTokens(result);
    return result.access_token;
  } catch {
    await clearTokens();
    return null;
  }
}
