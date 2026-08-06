'use client';

// Mounts once from the root layout. It initializes PostHog and owns the analytics
// identity lifecycle for every route, including /connect and the SnapTrade callback.

import { useEffect } from 'react';
import {
  identifyAnalyticsUser,
  initAnalytics,
  resetAnalyticsUser,
} from '@/lib/analytics';
import { getSupabase } from '@/lib/supabase';

export function Analytics() {
  useEffect(() => {
    initAnalytics();
    const supabase = getSupabase();
    if (!supabase) {
      resetAnalyticsUser();
      return;
    }

    let active = true;
    const syncIdentity = (userId: string | null | undefined) => {
      if (!active) return;
      if (userId) identifyAnalyticsUser(userId);
      else resetAnalyticsUser();
    };

    // Cover a session restored from browser storage before this listener mounted.
    void supabase.auth.getSession().then(({ data }) => {
      syncIdentity(data.session?.user.id);
    });

    // Session presence is the invariant. SIGNED_IN may repeat and TOKEN_REFRESHED
    // is expected; the helper is idempotent for the same UUID. A null session,
    // including SIGNED_OUT, resets PostHog's persisted person/device association.
    const { data: subscription } = supabase.auth.onAuthStateChange((_event, session) => {
      syncIdentity(session?.user.id);
    });

    return () => {
      active = false;
      subscription.subscription.unsubscribe();
    };
  }, []);

  return null;
}
