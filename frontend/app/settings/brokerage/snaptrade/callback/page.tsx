'use client';

import { Suspense, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  brokerageErrorMessage,
  verifySnapTradeConnection,
} from '@/lib/brokerage';
import {
  trackBrokerConnectionCompleted,
  trackBrokerConnectionFailed,
} from '@/lib/analytics';
import { getAccessToken } from '@/lib/supabase';

const CONNECTION_ID = /^[A-Za-z0-9_-]{1,128}$/;

function CallbackContent() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const status = (params.get('status') ?? '').toUpperCase();
    const connectionId = params.get('connection_id') ?? '';

    // Remove provider identifiers from the address bar/history immediately. The
    // analytics sanitizer is defense-in-depth for the earlier automatic pageview.
    window.history.replaceState(null, '', window.location.pathname);

    async function verify() {
      if (status !== 'SUCCESS' || !CONNECTION_ID.test(connectionId)) {
        const code = 'snaptrade_callback_invalid';
        if (active) setError(code);
        trackBrokerConnectionFailed('snaptrade', code);
        return;
      }
      const token = await getAccessToken();
      if (!token) {
        const code = 'authentication_required';
        if (active) setError(code);
        trackBrokerConnectionFailed('snaptrade', code);
        return;
      }
      const result = await verifySnapTradeConnection(token, connectionId);
      if (!active) return;
      if (!result.ok) {
        setError(result.error);
        trackBrokerConnectionFailed('snaptrade', result.error);
        return;
      }
      trackBrokerConnectionCompleted('snaptrade', result.data.accounts.length);
      router.replace('/connect?snaptrade=connected');
    }

    void verify();
    return () => {
      active = false;
    };
  }, [params, router]);

  return (
    <main className="flex min-h-screen justify-center bg-gradient-to-br from-[#2c2c2a] to-[#444441] p-6">
      <div className="mt-16 h-fit w-full max-w-md rounded-3xl bg-bg p-6 text-text shadow-2xl">
        <h1 className="text-lg font-semibold">Finishing brokerage connection</h1>
        {error ? (
          <>
            <p role="alert" className="mt-3 text-[13px] text-red">
              {brokerageErrorMessage(error)}
            </p>
            <a href="/connect" className="mt-4 inline-block text-sm text-accent underline underline-offset-2">
              Return to connections
            </a>
          </>
        ) : (
          <p className="mt-3 text-[13px] text-text-3">Verifying the connection and loading accounts…</p>
        )}
      </div>
    </main>
  );
}

export default function SnapTradeCallbackPage() {
  return (
    <Suspense fallback={null}>
      <CallbackContent />
    </Suspense>
  );
}
