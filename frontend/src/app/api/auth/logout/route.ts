import { NextResponse } from "next/server";
import { clearTokens, frontendUrl } from "@/lib/auth";

export async function POST(request: Request) {
  if (request.headers.get("origin") !== new URL(frontendUrl).origin) return new Response("Forbidden", { status: 403 });
  await clearTokens();
  return NextResponse.redirect(frontendUrl, 303);
}
