import type { Source, ThesisData } from '@/lib/widgets';
import { SafeHtml, Sources, WidgetCard } from './Sources';

export function Thesis({ data, sources }: { data: ThesisData; sources: Source[] }) {
  return (
    <WidgetCard eyebrow="Thesis · drafted from your research">
      <div className="flex items-center gap-3 mb-3">
        <div className="flex-1">
          <div className="text-[22px] font-semibold -tracking-tight">{data.ticker}</div>
        </div>
        <div className="text-[11px] font-bold tracking-[0.12em] px-3.5 py-2 rounded-md bg-accent-bg text-accent">
          {data.rating}
        </div>
      </div>

      {/* Conviction strip */}
      <div className="grid grid-cols-[auto_1fr_1fr] gap-3 bg-bg border border-border rounded-xl px-3.5 py-2.5 mb-3.5 items-center">
        <span className="text-[10px] font-bold tracking-[0.12em] bg-accent-bg text-accent px-2.5 py-1 rounded">
          CONVICTION
        </span>
        <ConvictionItem label="Horizon" value={data.horizon} />
        <ConvictionItem label="Weight" value={`${data.weight_pct_nav}% NAV`} />
      </div>

      {/* TLDR */}
      <div className="bg-accent-bg border-l-2 border-accent rounded-md px-3.5 py-2.5 mb-3.5 text-[13.5px] leading-relaxed text-text">
        <strong className="text-accent">TL;DR</strong> · <SafeHtml html={data.tldr_html} />
      </div>

      <Section title="Why I'm in">
        {data.reasons_to_be_in.map((r, i) => (
          <Bullet key={i} kind="up"><SafeHtml html={r} /></Bullet>
        ))}
      </Section>

      <Section title="What to watch · weekly">
        {data.what_to_watch_weekly.map((w, i) => (
          <Bullet key={i} kind="accent"><span>{w}</span></Bullet>
        ))}
      </Section>

      <Section title="Thesis-breakers · exit signals">
        {data.thesis_breakers.map((b, i) => (
          <Bullet key={i} kind="down"><span>{b}</span></Bullet>
        ))}
      </Section>

      <Sources sources={sources} />
    </WidgetCard>
  );
}

function ConvictionItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-[11px] text-text-3 uppercase tracking-[0.08em]">
      {label}
      <div className="text-[14px] font-semibold text-text normal-case tracking-normal mt-0.5">{value}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mb-3.5 last:mb-0">
      <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-text-3 mb-2">{title}</div>
      <ul className="flex flex-col gap-1.5">{children}</ul>
    </section>
  );
}

function Bullet({ kind, children }: { kind: 'up' | 'down' | 'accent'; children: React.ReactNode }) {
  const sym = kind === 'up' ? '▲' : kind === 'down' ? '▼' : '●';
  const color = kind === 'up' ? 'text-green-DEFAULT' : kind === 'down' ? 'text-red-DEFAULT' : 'text-accent';
  return (
    <li className="flex gap-2 items-start text-[13px] leading-snug">
      <span className={`${color} font-bold text-[11px] leading-snug`}>{sym}</span>
      <span className="flex-1">{children}</span>
    </li>
  );
}
