'use client';

import { Suspense, useEffect, useRef, useState } from 'react';
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

type CallbackSnapshot = {
  status: string;
  connectionId: string;
};

type VerificationOutcome =
  | { ok: true; accountCount: number }
  | { ok: false; error: string };

function CallbackContent() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const callbackSnapshotRef = useRef<CallbackSnapshot | null>(null);
  const verificationPromiseRef = useRef<Promise<VerificationOutcome> | null>(null);
  const terminalOutcomeRef = useRef<'completed' | 'failed' | null>(null);
  const urlScrubbedRef = useRef(false);

  // Capture the provider result during the initial render. Next synchronizes native
  // history changes with useSearchParams, so these values must outlive URL scrubbing.
  if (callbackSnapshotRef.current === null) {
    callbackSnapshotRef.current = {
      status: (params.get('status') ?? '').toUpperCase(),
      connectionId: params.get('connection_id') ?? '',
    };
  }

  useEffect(() => {
    let active = true;
    const callback = callbackSnapshotRef.current;

    if (!urlScrubbedRef.current) {
      urlScrubbedRef.current = true;
      // Remove provider identifiers from the address bar/history immediately. The
      // analytics sanitizer remains defense-in-depth for the initial pageview.
      window.history.replaceState(null, '', window.location.pathname);
    }

    async function verify(): Promise<VerificationOutcome> {
      if (!callback || callback.status !== 'SUCCESS' || !CONNECTION_ID.test(callback.connectionId)) {
        return { ok: false, error: 'snaptrade_callback_invalid' };
      }

      const token = await getAccessToken();
      if (!token) return { ok: false, error: 'authentication_required' };

      const result = await verifySnapTradeConnection(token, callback.connectionId);
      if (!result.ok) return { ok: false, error: result.error };
      return { ok: true, accountCount: result.data.accounts.length };
    }

    // React Strict Mode may set up, clean up, and set up this effect again. Reuse the
    // in-flight promise so verification is sent once, while the active lifecycle can
    // still observe its result after the earlier lifecycle has been cleaned up.
    if (verificationPromiseRef.current === null) {
      verificationPromiseRef.current = verify();
    }

    void verificationPromiseRef.current.then((outcome) => {
      if (!active || terminalOutcomeRef.current !== null) return;

      if (!outcome.ok) {
        terminalOutcomeRef.current = 'failed';
        setError(outcome.error);
        trackBrokerConnectionFailed('snaptrade', outcome.error);
        return;
      }

      terminalOutcomeRef.current = 'completed';
      trackBrokerConnectionCompleted('snaptrade', outcome.accountCount);
      router.replace('/connect?snaptrade=connected');
    });

    return () => {
      active = false;
    };
  }, [router]);

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
