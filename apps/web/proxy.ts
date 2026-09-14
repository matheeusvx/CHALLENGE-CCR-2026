import { NextRequest, NextResponse } from "next/server";

const SESSION_COOKIE = "motiva_beta_session";
const PROXY_HEADER = "X-Beta-Proxy-Secret";

function backendUrl(request: NextRequest): URL | null {
  const configured = process.env.BACKEND_API_URL?.trim() || "http://localhost:8000";
  try {
    const url = new URL(request.nextUrl.pathname + request.nextUrl.search, configured);
    if (process.env.VERCEL && url.protocol !== "https:") return null;
    return url;
  } catch {
    return null;
  }
}

function privateHeaders(request: NextRequest, secret: string): Headers {
  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("x-operator-scope");
  headers.set(PROXY_HEADER, secret);
  return headers;
}

export async function proxy(request: NextRequest) {
  const destination = backendUrl(request);
  const secret = process.env.BETA_PROXY_SECRET?.trim();

  if (request.nextUrl.pathname.startsWith("/api/")) {
    if (!destination || !secret) {
      return NextResponse.json({ error: { code: "PROXY_NOT_CONFIGURED", message: "Serviço indisponível.", details: [] } }, { status: 503 });
    }
    return NextResponse.rewrite(destination, {
      request: { headers: privateHeaders(request, secret) },
    });
  }

  const isLogin = request.nextUrl.pathname === "/login";
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  let authenticated = false;
  if (token && destination && secret) {
    const meUrl = new URL("/api/auth/me", destination);
    try {
      const response = await fetch(meUrl, {
        headers: privateHeaders(request, secret),
        cache: "no-store",
      });
      authenticated = response.ok;
    } catch {
      authenticated = false;
    }
  }
  if (!authenticated && !isLogin) return NextResponse.redirect(new URL("/login", request.url));
  if (authenticated && isLogin) return NextResponse.redirect(new URL("/", request.url));
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
