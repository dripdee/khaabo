import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { AuthContext } from "@/hooks/authContext";
import type { AuthContextValue } from "@/hooks/authContext";
import { STALE, queryKeys } from "@/lib/queryClient";
import { auth, isAuthConfigured } from "@/lib/supabase";
import { setTokenGetter } from "@/services/client";
import { usersApi } from "@/services/endpoints";

// Registered once at module load so the HTTP client can attach the bearer token
// without importing the auth module (which would create a cycle).
setTokenGetter(() => auth.getToken());

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [hasToken, setHasToken] = useState(() => Boolean(auth.getToken()));

  useEffect(
    () =>
      auth.subscribe(() => {
        setHasToken(Boolean(auth.getToken()));
        void queryClient.invalidateQueries({ queryKey: queryKeys.me });
      }),
    [queryClient],
  );

  const { data: me, isLoading } = useQuery({
    queryKey: queryKeys.me,
    queryFn: usersApi.me,
    // Only ask the API who we are when a token exists; otherwise this would 401 on
    // every anonymous page load.
    enabled: hasToken,
    staleTime: STALE.me,
    retry: false,
  });

  const signInMutation = useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      auth.signIn(email, password),
  });

  const value = useMemo<AuthContextValue>(
    () => ({
      me: me ?? null,
      isLoading: hasToken && isLoading,
      isSignedIn: Boolean(me),
      isModerator: me?.role === "moderator" || me?.role === "admin",
      isAdmin: me?.role === "admin",
      isConfigured: isAuthConfigured,
      signIn: async (email, password) => {
        await signInMutation.mutateAsync({ email, password });
      },
      signUp: (email, password) => auth.signUp(email, password),
      signInAsDev: (email, role) => {
        auth.signInAsDev(email, role);
      },
      signOut: async () => {
        await auth.signOut();
        // Clear everything: cached responses may include per-user fields such as
        // `liked_by_me`, which must not survive a sign-out.
        queryClient.clear();
      },
    }),
    [me, hasToken, isLoading, signInMutation, queryClient],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
