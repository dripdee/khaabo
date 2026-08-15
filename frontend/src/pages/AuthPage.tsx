import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { useAuth } from "@/hooks/authContext";
import { Seo } from "@/lib/seo";

/**
 * Auth.
 *
 * Supabase-backed. When Supabase is not configured the page says so plainly and
 * offers the development bypass rather than failing with an opaque error — a
 * contributor should be able to run the product without creating an account first.
 */
export default function AuthPage() {
  const navigate = useNavigate();
  const { signIn, signUp, signInAsDev, isConfigured, isSignedIn, signOut, me } = useAuth();

  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setNotice(null);
    setBusy(true);

    try {
      if (mode === "signin") {
        await signIn(email, password);
        navigate("/");
      } else {
        const result = await signUp(email, password);
        if (result.needsConfirmation) {
          setNotice("Check your inbox to confirm the address, then sign in.");
        } else {
          navigate("/");
        }
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Authentication failed");
    } finally {
      setBusy(false);
    }
  }

  if (isSignedIn) {
    return (
      <div className="mx-auto max-w-md px-4 py-20 text-center">
        <Seo title="Account" description="Your Khaabo account." noIndex />
        <h1 className="font-display text-hero text-text">Signed in</h1>
        <p className="mt-3 text-muted">{me?.email ?? me?.profile?.username}</p>
        <div className="mt-8 flex justify-center gap-2">
          <Button variant="secondary" onClick={() => navigate("/bookmarks")}>
            My saves
          </Button>
          <Button
            variant="ghost"
            onClick={async () => {
              await signOut();
              navigate("/");
            }}
          >
            Sign out
          </Button>
        </div>
      </div>
    );
  }

  return (
    <>
      <Seo title="Sign in" description="Sign in to review, save and follow dishes." noIndex />

      <div className="mx-auto max-w-md px-4 py-16">
        <h1 className="text-center font-display text-hero text-text">
          {mode === "signin" ? "Welcome back" : "Join Khaabo"}
        </h1>
        <p className="mt-3 text-center text-muted">
          An account lets you review, save dishes and earn badges.
        </p>

        <Card className="mt-8">
          {!isConfigured && (
            <div className="mb-5 rounded-input border border-warning/30 bg-warning/10 p-4 text-sm">
              <p className="font-medium text-warning">Auth is not configured</p>
              <p className="mt-1 text-muted">
                Set <code className="text-xs">VITE_SUPABASE_URL</code> and{" "}
                <code className="text-xs">VITE_SUPABASE_ANON_KEY</code> to enable real accounts.
                In development you can continue with a bypass token.
              </p>
              <div className="mt-3 flex gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    signInAsDev("dev@example.com", "user");
                    navigate("/");
                  }}
                >
                  Continue as dev user
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    signInAsDev("admin@example.com", "admin");
                    navigate("/admin");
                  }}
                >
                  Continue as admin
                </Button>
              </div>
            </div>
          )}

          <form onSubmit={onSubmit} className="space-y-4">
            <div>
              <label htmlFor="email" className="mb-1.5 block text-sm text-muted">
                Email
              </label>
              <input
                id="email"
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="h-12 w-full rounded-input border border-border bg-surface px-4 text-text
                  placeholder:text-subtle focus:border-accent/60 focus:outline-none"
                placeholder="you@example.com"
              />
            </div>

            <div>
              <label htmlFor="password" className="mb-1.5 block text-sm text-muted">
                Password
              </label>
              <input
                id="password"
                type="password"
                required
                minLength={8}
                autoComplete={mode === "signin" ? "current-password" : "new-password"}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="h-12 w-full rounded-input border border-border bg-surface px-4 text-text
                  placeholder:text-subtle focus:border-accent/60 focus:outline-none"
                placeholder="At least 8 characters"
              />
            </div>

            {error && (
              <p role="alert" className="rounded-input bg-negative/10 px-4 py-3 text-sm text-negative">
                {error}
              </p>
            )}
            {notice && (
              <p className="rounded-input bg-positive/10 px-4 py-3 text-sm text-positive">
                {notice}
              </p>
            )}

            <Button type="submit" fullWidth loading={busy} disabled={!isConfigured}>
              {mode === "signin" ? "Sign in" : "Create account"}
            </Button>
          </form>

          <p className="mt-5 text-center text-sm text-subtle">
            {mode === "signin" ? "New here?" : "Already have an account?"}{" "}
            <button
              type="button"
              onClick={() => {
                setMode(mode === "signin" ? "signup" : "signin");
                setError(null);
                setNotice(null);
              }}
              className="text-accent hover:underline"
            >
              {mode === "signin" ? "Create an account" : "Sign in"}
            </button>
          </p>
        </Card>
      </div>
    </>
  );
}
