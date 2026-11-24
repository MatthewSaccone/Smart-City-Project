import { randomBytes, createHash } from "node:crypto";
import { cookies } from "next/headers";
import { NextResponse } from "next/server";
import { issuerBase, clientId, redirectUri, cookieOptions } from "@/lib/auth";

export async function GET() {
  const state = randomBytes(32).toString("base64url");
  const verifier = randomBytes(48).toString("base64url");
  const jar = await cookies();
  jar.set("sc_oauth_state", state, { ...cookieOptions, maxAge: 600 });
  jar.set("sc_oauth_verifier", verifier, { ...cookieOptions, maxAge: 600 });
  const url = new URL(`${issuerBase}/application/o/authorize/`);
  url.search = new URLSearchParams({ client_id: clientId, response_type: "code", redirect_uri: redirectUri,
    scope: "openid smart-city offline_access", state, code_challenge_method: "S256",
    code_challenge: createHash("sha256").update(verifier).digest("base64url") }).toString();
  return NextResponse.redirect(url);
}
