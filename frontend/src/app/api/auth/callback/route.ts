import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { redirectUri, frontendUrl, cookieOptions, exchange, saveTokens, clearTokens } from "@/lib/auth";

export async function GET(request: Request) {
  const params = new URL(request.url).searchParams;
  const jar = await cookies();
  const state = jar.get("sc_oauth_state")?.value;
  const verifier = jar.get("sc_oauth_verifier")?.value;
  const code = params.get("code");
  jar.set("sc_oauth_state", "", { ...cookieOptions, maxAge: 0 });
  jar.set("sc_oauth_verifier", "", { ...cookieOptions, maxAge: 0 });
  if (!state || !verifier || !code || params.get("state") !== state || params.has("error")) return new Response("Login could not be completed. Return to the dashboard and sign in again.", { status: 400 });
  try {
    await clearTokens();
    await saveTokens(await exchange({ grant_type: "authorization_code", code, redirect_uri: redirectUri, code_verifier: verifier }));
    return NextResponse.redirect(frontendUrl);
  } catch {
    return new Response("Login could not be completed. Return to the dashboard and sign in again.", { status: 401 });
  }
}
