import { QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { HelmetProvider } from "react-helmet-async";
import { BrowserRouter } from "react-router-dom";

import { App } from "@/App";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AuthProvider } from "@/providers/AuthProvider";
import { queryClient } from "@/lib/queryClient";
import { captureRedirectedSession } from "@/lib/supabase";
import "@/styles/index.css";

// Email-confirmation redirects land with the session in the URL fragment; pick
// it up before React mounts so the first render already knows the user.
captureRedirectedSession();

const container = document.getElementById("root");
if (!container) throw new Error("Root element #root is missing from index.html");

ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <ErrorBoundary>
      <HelmetProvider>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            {/* AuthProvider sits inside the query provider because it reads `/users/me`
                through TanStack Query. */}
            <AuthProvider>
              <App />
            </AuthProvider>
          </BrowserRouter>
        </QueryClientProvider>
      </HelmetProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
