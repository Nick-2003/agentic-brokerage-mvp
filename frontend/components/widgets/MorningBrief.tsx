import type { MorningBriefData, Source } from '@/lib/widgets';
import { AccountBadge, SafeHtml, Sources, WidgetCard } from './Sources';

export function MorningBrief({ data, sources }: { data: MorningBriefData; sources: Source[] }) {
  return (
    <WidgetCard eyebrow="Morning brief" headerRight={<AccountBadge label={data.account_label} />}>
      <div className="text-base font-semibold mb-2 -tracking-tight">
        <SafeHtml html={data.headline} />
      </div>
      <div className="space-y-2 text-[14px] leading-relaxed text-text">
        {data.paragraphs.map((p, i) => (
          <p key={i}>
            <SafeHtml html={p} />
          </p>
        ))}
      </div>
      {/* 061 — data-freshness footnote: what day the figures are from + when
          generated. Italic, muted; only shown when the agent copied it through. */}
      {data.as_of_note && (
        <p className="mt-3 text-[11.5px] italic leading-snug text-text-3">
          <SafeHtml html={data.as_of_note} />
        </p>
      )}
      <Sources sources={sources} />
    </WidgetCard>
  );
}
