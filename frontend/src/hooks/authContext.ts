/**
 * Auth context and hook.
 *
 * Separated from the provider component so this module exports no components — that
 * keeps fast refresh working and lets non-component code read the context type.
 */
import { createContext, useContext } from "react";

import type { Me } from "@/types/api";

export interface AuthContextValue {
  me: Me | null;
  isLoading: boolean;
  isSignedIn: boolean;
  isModerator: boolean;
  isAdmin: boolean;
  isConfigured: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<{ needsConfirmation: boolean }>;
  signInAsDev: (email?: string, role?: "user" | "admin") => void;
  signOut: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return context;
}
