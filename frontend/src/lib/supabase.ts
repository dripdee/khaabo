/**
 * Supabase auth.
 *
 * Only the anon key reaches the browser — it is public by design. The service key
 * and JWT secret live on the backend only.
 *
 * Supabase is loaded lazily and treated as optional: with no credentials configured
 * the app still runs read-only (and, in development, `AUTH_DEV_BYPASS` tokens work),
 * so a contributor can run the product without creating a Supabase project.
 */
const SUPABASE_URL = (import.meta.env.VITE_SUPABASE_URL as string | undefined) ?? "";
const SUPABASE_ANON_KEY = (import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined) ?? "";

export const isAuthConfigured = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);

const STORAGE_KEY = "khaabo:session";

export interface Session {
  access_token: string;
  refresh_token?: string;
  expires_at?: number;
  user: {
    id: string;
    email?: string;
  };
}

function readSession(): Session | null {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    const session = JSON.parse(raw) as Session;
    if (session.expires_at && session.expires_at * 1000 < Date.now()) {
      // Expired tokens are cleared eagerly so the UI does not show a signed-in
      // state that the API will reject.
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return session;
  } catch {
    return null;
  }
}

function writeSession(session: Session | null): void {
  if (session) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
  window.dispatchEvent(new Event("khaabo:auth"));
}

async function authRequest<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${SUPABASE_URL}/auth/v1${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      apikey: SUPABASE_ANON_KEY,
    },
    body: JSON.stringify(body),
  });

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      (payload as { error_description?: string; msg?: string } | null)?.error_description ??
      (payload as { msg?: string } | null)?.msg ??
      "Authentication failed";
    throw new Error(message);
  }
  return payload as T;
}

/**
 * After email confirmation Supabase redirects back with the session in the URL
 * fragment (`#access_token=...&refresh_token=...&expires_in=...`). Capture it as
 * a local session and clean the URL so the token never survives a reload/share.
 * Returns true when a redirected session was consumed.
 */
export function captureRedirectedSession(): boolean {
  if (typeof window === "undefined") return false;
  const fragment = window.location.hash.replace(/^#/, "");
  if (!fragment || !fragment.includes("access_token=")) return false;

  const params = new URLSearchParams(fragment);
  const accessToken = params.get("access_token");
  if (!accessToken) return false;

  const payload = decodeJwtPayload(accessToken);
  const expiresIn = Number(params.get("expires_in") ?? "0");
  const expiresAtFromToken = payload?.exp ? Number(payload.exp) : 0;
  const expiresAt = expiresAtFromToken || (expiresIn ? Math.floor(Date.now() / 1000) + expiresIn : undefined);

  writeSession({
    access_token: accessToken,
    refresh_token: params.get("refresh_token") ?? undefined,
    expires_at: expiresAt,
    user: {
      id: payload?.sub ?? "supabase-user",
      email: payload?.email ?? undefined,
    },
  });

  const cleanUrl = window.location.pathname + window.location.search;
  window.history.replaceState(null, "", cleanUrl);
  return true;
}

function decodeJwtPayload(token: string): { sub?: string; email?: string; exp?: number } | null {
  try {
    const [, payload] = token.split(".");
    if (!payload) return null;
    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
    const json = decodeURIComponent(
      atob(normalized)
        .split("")
        .map((char) => `%${char.charCodeAt(0).toString(16).padStart(2, "0")}`)
        .join(""),
    );
    return JSON.parse(json);
  } catch {
    return null;
  }
}

export const auth = {
  getSession: readSession,

  async signIn(email: string, password: string): Promise<Session> {
    if (!isAuthConfigured) {
      throw new Error("Authentication is not configured for this deployment.");
    }
    const session = await authRequest<Session>("/token?grant_type=password", {
      email,
      password,
    });
    writeSession(session);
    return session;
  },

  async signUp(email: string, password: string): Promise<{ needsConfirmation: boolean }> {
    if (!isAuthConfigured) {
      throw new Error("Authentication is not configured for this deployment.");
    }
    const result = await authRequest<{ access_token?: string; user?: { id: string } }>("/signup", {
      email,
      password,
    });

    // Projects with email confirmation enabled return no token; the UI must say so
    // rather than pretending the user is signed in.
    if (result.access_token) {
      writeSession(result as unknown as Session);
      return { needsConfirmation: false };
    }
    return { needsConfirmation: true };
  },

  async signOut(): Promise<void> {
    const session = readSession();
    writeSession(null);
    if (!session || !isAuthConfigured) return;

    await fetch(`${SUPABASE_URL}/auth/v1/logout`, {
      method: "POST",
      headers: {
        apikey: SUPABASE_ANON_KEY,
        Authorization: `Bearer ${session.access_token}`,
      },
    }).catch(() => undefined);
  },

  /** Development helper. Only accepted while the backend has AUTH_DEV_BYPASS on. */
  signInAsDev(email = "dev@example.com", role: "user" | "admin" = "user"): Session {
    const session: Session = {
      access_token: role === "admin" ? `dev:${email}:admin` : `dev:${email}`,
      user: { id: "dev", email },
    };
    writeSession(session);
    return session;
  },

  getToken(): string | null {
    return readSession()?.access_token ?? null;
  },

  subscribe(listener: () => void): () => void {
    window.addEventListener("khaabo:auth", listener);
    window.addEventListener("storage", listener);
    return () => {
      window.removeEventListener("khaabo:auth", listener);
      window.removeEventListener("storage", listener);
    };
  },
};
