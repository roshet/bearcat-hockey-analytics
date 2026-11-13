/* The site's only lock: a shared password, checked before every request -- pages and the
   /data files alike -- by Vercel Routing Middleware. The site stays private until the ACHA
   has been asked for permission (docs/data-sources.md, "Permission posture").

   The password lives in the Vercel environment variable SITE_PASSWORD, set by the owner
   (`vercel env add SITE_PASSWORD production`). Without it, nothing is served. */

declare const process: { env: Record<string, string | undefined> };

const REALM = 'Basic realm="Bearcat Hockey Analytics", charset="UTF-8"';

export const config = { matcher: "/:path*" };

/** Equal-time comparison, so response timing doesn't leak how much of a guess was right. */
function same(a: string, b: string): boolean {
  const x = new TextEncoder().encode(a);
  const y = new TextEncoder().encode(b);
  let diff = x.length ^ y.length;
  for (let i = 0; i < Math.max(x.length, y.length); i++) diff |= (x[i] ?? 0) ^ (y[i] ?? 0);
  return diff === 0;
}

/** Whether an Authorization header carries the password (any username). */
export function authorized(header: string | null, password: string | undefined): boolean {
  // A blank or whitespace-only password counts as none set: fail closed.
  if (!password?.trim() || !header) return false;
  const [scheme, encoded] = header.split(" ");
  if (scheme?.toLowerCase() !== "basic" || !encoded) return false;
  let decoded: string;
  try {
    decoded = new TextDecoder().decode(Uint8Array.from(atob(encoded), (c) => c.charCodeAt(0)));
  } catch {
    return false;
  }
  const colon = decoded.indexOf(":");
  if (colon < 0) return false;
  const given = decoded.slice(colon + 1);
  return given.length > 0 && same(given, password);
}

/** Continue (undefined) with the right password; otherwise ask for it. */
export default function middleware(request: Request): Response | undefined {
  if (authorized(request.headers.get("authorization"), process.env.SITE_PASSWORD)) return undefined;
  return new Response("Password required.", {
    status: 401,
    headers: { "WWW-Authenticate": REALM, "Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow" },
  });
}
