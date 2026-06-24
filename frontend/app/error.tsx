'use client';

// 055 — route-level error boundary (Next App Router). A client-side render error
// in the home route previously white-screened the whole app with Next's default
// "Application error: a client-side exception has occurred". This catches it and
// shows a friendly, recoverable fallback instead (with a Try-again reset).
// (Per-widget errors are additionally contained by WidgetErrorBoundary, so a
// single bad card never reaches this boundary.)

import { useEffect } from 'react';

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surfaces in the browser console (and any error reporter) for diagnosis.
    console.error('App route error:', error);
  }, [error]);

  return (
    <main className="min-h-screen flex items-center justify-center p-6 text-center bg-bg">
      <div className="max-w-sm">
        <div className="text-[15px] font-semibold text-text mb-1">Something went wrong</div>
        <p className="text-[13px] text-text-3 mb-4">
          The app hit an unexpected error. This is usually transient — try again.
        </p>
        <button
          onClick={() => reset()}
          className="px-4 py-2 rounded-full bg-accent text-white text-[13px] font-semibold active:scale-95 transition-transform"
        >
          Try again
        </button>
      </div>
    </main>
  );
}
