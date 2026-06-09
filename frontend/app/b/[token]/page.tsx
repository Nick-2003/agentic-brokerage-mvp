// Public brief permalink page (W6.3) — /b/<token>.
//
// Renders the full WhatsApp briefing as a readable web page. The token is the
// capability (no sign-in); the backend serves it via GET /api/brief/{token} and
// returns 404 when missing/expired → we show a friendly "not found". The brief
// uses WhatsApp markup (*bold*, _italic_), rendered here WITHOUT HTML injection
// (a small token parser → React <strong>/<em>, so no XSS surface).

'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import { fetchBrief, type PublishedBrief } from '@/lib/brief';

// *bold* / _italic_ → React nodes, escape-free (no dangerouslySetInnerHTML).
function renderInline(s: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const re = /(\*[^*]+\*|_[^_]+_)/g;
  let last = 0;
  let key = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s)) !== null) {
    if (m.index > last) out.push(s.slice(last, m.index));
    const tok = m[0];
    out.push(
      tok.startsWith('*') ? (
        <strong key={key++}>{tok.slice(1, -1)}</strong>
      ) : (
        <em key={key++}>{tok.slice(1, -1)}</em>
      ),
    );
    last = m.index + tok.length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}

export default function BriefPage() {
  const params = useParams();
  const token = Array.isArray(params.token) ? params.token[0] : (params.token ?? '');
  // undefined = loading, null = not found, object = loaded
  const [brief, setBrief] = useState<PublishedBrief | null | undefined>(undefined);

  useEffect(() => {
    if (!token) {
      setBrief(null);
      return;
    }
    let active = true;
    fetchBrief(token).then((b) => {
      if (active) setBrief(b);
    });
    return () => {
      active = false;
    };
  }, [token]);

  return (
    <main className="min-h-screen bg-gradient-to-br from-[#2c2c2a] to-[#444441] flex justify-center p-6">
      <div className="w-full max-w-md self-start mt-8">
        <div className="text-[12px] font-semibold tracking-[0.08em] text-[#f5f5f0] opacity-70 mb-2">
          DAILY PORTFOLIO BRIEFING
        </div>
        <div className="bg-bg rounded-3xl shadow-2xl p-6 text-text">
          {brief === undefined ? (
            <p className="text-[13px] text-text-3">Loading…</p>
          ) : brief === null ? (
            <p className="text-[13px] text-text-3">
              This briefing link is invalid or has expired.
            </p>
          ) : (
            <article className="space-y-3 text-[14px] leading-relaxed">
              {(brief.text || '').split(/\n{2,}/).map((para, i) => (
                <p key={i} className="whitespace-pre-wrap">
                  {renderInline(para)}
                </p>
              ))}
              {brief.as_of && (
                <p className="pt-3 mt-2 border-t border-border text-[11px] text-text-3">
                  As of {brief.as_of}
                  {brief.account_id ? ` · ${brief.account_id}` : ''}
                </p>
              )}
            </article>
          )}
        </div>
        <p className="text-center text-[11px] text-[#f5f5f0] opacity-50 mt-4 pb-6">
          A private, expiring link. Reply <strong>STOP</strong> on WhatsApp to unsubscribe.
        </p>
      </div>
    </main>
  );
}
