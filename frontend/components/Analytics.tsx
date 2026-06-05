'use client';

// Mounts once (from app/layout.tsx) to initialise PostHog on the client.
// All the event-capture logic lives in lib/analytics.ts; this just runs init.
// initAnalytics() is a no-op when NEXT_PUBLIC_POSTHOG_API_KEY is missing or is
// the `phc_REPLACE` placeholder, so this is safe to mount unconditionally.

import { useEffect } from 'react';
import { initAnalytics } from '@/lib/analytics';

export function Analytics() {
  useEffect(() => {
    initAnalytics();
  }, []);
  return null;
}
