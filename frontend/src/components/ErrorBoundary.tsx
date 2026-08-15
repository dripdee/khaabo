import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Top-level error boundary.
 *
 * Without this, a render error anywhere shows a blank page with no way back. It
 * deliberately offers a reload and a route home rather than only logging.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error("Unhandled UI error", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div className="grid min-h-screen place-items-center bg-bg px-4">
        <div className="max-w-md text-center">
          <h1 className="font-display text-hero text-text">Something broke</h1>
          <p className="mt-4 text-muted">
            The page hit an unexpected error. Reloading usually clears it.
          </p>
          <pre className="mt-4 max-h-40 overflow-auto rounded-input bg-surface-2 p-3 text-left text-xs text-subtle">
            {this.state.error.message}
          </pre>
          <div className="mt-6 flex justify-center gap-2">
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded-input bg-accent px-4 py-2.5 text-sm font-semibold text-black"
            >
              Reload
            </button>
            <a
              href="/"
              className="rounded-input border border-border px-4 py-2.5 text-sm text-text"
            >
              Go home
            </a>
          </div>
        </div>
      </div>
    );
  }
}
