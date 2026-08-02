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
import { fetchBrief, type PublishedBrief, type BriefChartData } from '@/lib/brief';

// 051/057 — per-holding day-P&L bar chart. Diverging magnitude bars from a centre
// axis: gainers (green) extend RIGHT, losers (red) extend LEFT; bar length ∝
// |P&L| / max. Pure CSS (no chart lib / no SVG). 057: bar + value colors are
// INLINE hex (not Tailwind `bg-green-DEFAULT`/`text-...` classes, which weren't
// rendering on this route), bars are taller, and a tiny floor keeps small moves
// visible — so the magnitude/direction reads clearly.
const PNL_GREEN = '#0F6E56';
const PNL_RED = '#C0392B';

function DayPnlBars({ data }: { data: BriefChartData }) {
  const bars = data.bars ?? [];
  if (bars.length === 0) return null;
  const max = Math.max(...bars.map((b) => Math.abs(b.day_pnl)), 1);
  return (
    <div className="pt-4 mt-2 border-t border-border">
      <div className="text-[11px] font-semibold uppercase tracking-[0.1em] text-text-3 mb-3">
        Day P&amp;L by holding
      </div>
      <div className="space-y-2">
        {bars.map((b) => {
          const up = b.day_pnl >= 0;
          const color = up ? PNL_GREEN : PNL_RED;
          // Bar length as a % of the half-track (each side is 50% of the track).
          // Floor at ~2% so a non-zero move always shows a sliver; 0 → nothing.
          const mag = Math.abs(b.day_pnl);
          const w = mag === 0 ? 0 : Math.max((mag / max) * 50, 2);
          return (
            <div key={b.symbol} className="flex items-center gap-2 text-[12px]">
              <span className="w-14 shrink-0 font-semibold text-text truncate">{b.symbol}</span>
              <div className="relative flex-1 h-5">
                {/* centre axis */}
                <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
                {/* the magnitude bar (inline color so it always renders) */}
                <div
                  className="absolute top-1/2 -translate-y-1/2 h-3.5 rounded-[3px]"
                  style={{
                    backgroundColor: color,
                    width: `${w}%`,
                    ...(up ? { left: '50%' } : { right: '50%' }),
                  }}
                />
              </div>
              <span
                className="w-24 shrink-0 text-right tabular-nums font-medium"
                style={{ color }}
              >
                {b.day_pnl_display}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

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
    <main className="min-h-screen bg-[#070706] flex justify-center p-6">
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
              {brief.chart_data && (brief.chart_data.bars?.length ?? 0) > 0 && (
                <DayPnlBars data={brief.chart_data} />
              )}
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
