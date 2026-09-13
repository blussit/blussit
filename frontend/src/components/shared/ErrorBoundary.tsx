import { Component, type ReactNode } from "react";

/**
 * The app has no other safety net for a render-time crash — nothing here
 * catches it, so it takes the ENTIRE page down to blank white with no
 * message at all (confirmed: a bad/missing field on one card is enough).
 * A class component is the only way to catch this (no hook equivalent for
 * componentDidCatch/getDerivedStateFromError) — mounted once around the
 * whole route tree in App.tsx, so whatever crashes, the customer sees a
 * real "something went wrong" screen with a way back out, never a blank
 * page that looks like the site is down.
 *
 * `resetKey`: pass the current route (e.g. location.pathname) so navigating
 * away from the page that crashed and back to a different one clears the
 * error automatically, instead of the whole app staying stuck until a hard
 * reload.
 */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: string }, { hasError: boolean }> {
  state = { hasError: false };

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error: unknown, info: { componentStack: string }) {
    // eslint-disable-next-line no-console
    console.error("Unhandled render error:", error, info.componentStack);
  }

  componentDidUpdate(prevProps: { resetKey?: string }) {
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false });
    }
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-4 bg-[var(--color-bg-primary)] p-6 text-center">
        <p className="text-lg font-bold text-[var(--color-text-primary)]">Something went wrong</p>
        <p className="max-w-sm text-sm text-[var(--color-text-secondary)]">
          This page hit an unexpected error. Nothing you did caused this — try reloading, or head back to the homepage.
        </p>
        <div className="flex gap-3">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-xl border border-[#D9DDE3] px-4 py-2.5 text-sm font-semibold text-[var(--color-text-primary)] hover:bg-gray-50"
          >
            Reload page
          </button>
          <button
            type="button"
            onClick={() => {
              window.location.href = "/";
            }}
            className="rounded-xl bg-[#F5B400] px-4 py-2.5 text-sm font-bold text-white hover:bg-[#EAAA00]"
          >
            Go to homepage
          </button>
        </div>
      </div>
    );
  }
}
