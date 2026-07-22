import type { PortfolioRiskData, Source } from '@/lib/widgets';
import { AccountBadge, SafeHtml, Sources, WidgetCard } from './Sources';

export function PortfolioRisk({
  data,
  sources,
}: {
  data: PortfolioRiskData;
  sources: Source[];
}) {
  return (
    <WidgetCard eyebrow="Portfolio risk audit" headerRight={<AccountBadge label={data.account_label} />}>
      {/* Big risk score */}
      <div className="flex items-center gap-3.5 bg-red-bg/40 border border-red/20 rounded-xl px-4 py-3 mb-3.5">
        <div className="text-[28px] font-semibold text-red -tracking-tight leading-none">
          {data.risk_score.toFixed(1)}
          <span className="text-sm text-text-3 font-normal">/10</span>
        </div>
        <div className="flex-1">
          <div className="text-[13px] font-semibold">
            Risk score: <span className="text-red">{data.risk_label}</span>
          </div>
          <div className="text-[11.5px] text-text-2 mt-0.5">{data.risk_summary}</div>
        </div>
      </div>

      {/* Sector exposure */}
      <Section title="Sector exposure · of NAV">
        <div className="bg-surface border border-border rounded-lg px-3.5 py-2">
          {data.sector_exposure.map((s, i) => (
            <div key={i} className="flex items-center py-1.5 text-[12.5px] border-b border-border last:border-b-0">
              <div className="text-text font-medium w-[88px] shrink-0">{s.label}</div>
              <div className="flex-1 h-1.5 mx-3 bg-bg rounded overflow-hidden">
                <div
                  className={`h-full rounded ${
                    s.severity === 'danger'
                      ? 'bg-red'
                      : s.severity === 'warn'
                        ? 'bg-amber'
                        : 'bg-accent'
                  }`}
                  style={{ width: `${Math.min(100, s.pct)}%` }}
                />
              </div>
              <div className="text-text-2 font-semibold w-11 text-right shrink-0">{s.pct}%</div>
            </div>
          ))}
        </div>
      </Section>

      {/* Risk flags */}
      <Section title="Key risk flags">
        {data.flags.map((f, i) => (
          <div key={i} className="bg-surface border border-border rounded-xl px-3 py-2.5 mb-2 flex gap-2.5">
            <FlagIcon severity={f.severity} />
            <div className="flex-1">
              <div className="text-[13px] font-semibold leading-tight">{f.title}</div>
              <div className="text-[12px] text-text-2 leading-snug mt-0.5">
                <SafeHtml html={f.detail_html} />
              </div>
            </div>
          </div>
        ))}
      </Section>

      {/* Suggestions */}
      <Section title="Suggested actions">
        {data.suggestions.map((s, i) => (
          <div
            key={i}
            className="bg-accent-bg border-l-2 border-accent rounded-md px-3 py-2.5 mb-2 text-[12.5px] leading-snug text-text last:mb-0"
          >
            <SafeHtml html={s} />
          </div>
        ))}
      </Section>

      <Sources sources={sources} />
    </WidgetCard>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-3.5 last:mb-0">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-3 mb-2">{title}</div>
      {children}
    </section>
  );
}

function FlagIcon({ severity }: { severity: 'low' | 'med' | 'high' }) {
  const [bg, color, sym] =
    severity === 'high'
      ? ['bg-red-bg', 'text-red', '⚠']
      : severity === 'med'
        ? ['bg-amber-bg', 'text-amber', '!']
        : ['bg-green-bg', 'text-green', '●'];
  return (
    <div className={`w-6 h-6 rounded-md ${bg} ${color} flex items-center justify-center text-[13px] flex-shrink-0`}>
      {sym}
    </div>
  );
}
