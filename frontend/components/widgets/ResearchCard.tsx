import type { ResearchCardData, Source } from '@/lib/widgets';
import { SafeHtml, Sources, WidgetCard } from './Sources';

function ratingClasses(r: ResearchCardData['rating']) {
  if (r === 'BUY') return 'bg-green-bg text-green-DEFAULT';
  if (r === 'HOLD') return 'bg-amber-bg text-amber-DEFAULT';
  return 'bg-red-bg text-red-DEFAULT';
}

export function ResearchCard({ data, sources }: { data: ResearchCardData; sources: Source[] }) {
  // Proposal 008 — current_price can be null/undefined when no price source was
  // available (e.g. real-market mode with yfinance down and no FMP profile price).
  // Guard against it: render "—" and skip the upside calc rather than crash on
  // null.toFixed() / divide-by-null.
  const price =
    typeof data.current_price === 'number' && isFinite(data.current_price) ? data.current_price : null;
  const upside = price !== null ? ((data.target_price - price) / price) * 100 : null;
  const upPositive = upside !== null && upside >= 0;

  return (
    <WidgetCard eyebrow="Equity research · generated">
      {/* Head: ticker + name + rating pill */}
      <div className="flex items-center gap-3 mb-3.5">
        <div className="flex-1 min-w-0">
          <div className="text-[22px] font-semibold -tracking-tight leading-tight">{data.ticker}</div>
          <div className="text-[11.5px] text-text-3 mt-0.5">{data.company_name}</div>
        </div>
        <div className={`text-[11px] font-bold tracking-[0.12em] px-3.5 py-2 rounded-md ${ratingClasses(data.rating)}`}>
          {data.rating}
        </div>
      </div>

      {/* Price target row */}
      <div className="flex items-center gap-3 bg-bg border border-border rounded-xl px-3.5 py-2.5 mb-4">
        <div>
          <div className="text-[10px] uppercase tracking-[0.1em] text-text-3 leading-tight">
            {data.horizon_months}-mo target
          </div>
          <div className="text-[11px] text-text-3 mt-0.5">
            vs {price !== null ? `${data.currency}${price.toFixed(2)}` : '—'}
          </div>
        </div>
        <div className="ml-auto text-right">
          <div className="text-[16px] font-semibold">
            {data.currency}{data.target_price.toLocaleString()}
          </div>
          {upside !== null && (
            <div className={`text-[12.5px] font-semibold ${upPositive ? 'text-green-DEFAULT' : 'text-red-DEFAULT'}`}>
              {upPositive ? '▲' : '▼'} {Math.abs(upside).toFixed(1)}%
            </div>
          )}
        </div>
      </div>

      {/* Thesis */}
      <section className="mb-3.5">
        <SectionTitle>Investment thesis</SectionTitle>
        <p className="text-[13.5px] leading-relaxed text-text">
          <SafeHtml html={data.thesis_html} />
        </p>
      </section>

      {/* Catalysts */}
      <section className="mb-3.5">
        <SectionTitle>Catalysts</SectionTitle>
        <ul className="flex flex-col gap-1.5">
          {data.catalysts.map((c, i) => (
            <li key={i} className="flex gap-2 items-start text-[13px] leading-snug">
              <span className="text-green-DEFAULT font-bold text-[11px] leading-snug">▲</span>
              <span className="flex-1"><SafeHtml html={c} /></span>
            </li>
          ))}
        </ul>
      </section>

      {/* Risks */}
      <section>
        <SectionTitle>Key risks</SectionTitle>
        <ul className="flex flex-col gap-1.5">
          {data.risks.map((r, i) => (
            <li key={i} className="flex gap-2 items-start text-[13px] leading-snug">
              <span className="text-red-DEFAULT font-bold text-[11px] leading-snug">▼</span>
              <span className="flex-1"><SafeHtml html={r} /></span>
            </li>
          ))}
        </ul>
      </section>

      <Sources sources={sources} />
    </WidgetCard>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-3 mb-2">
      {children}
    </div>
  );
}
