import type { MorningBriefData, Source } from '@/lib/widgets';
import { SafeHtml, Sources, WidgetCard } from './Sources';

export function MorningBrief({ data, sources }: { data: MorningBriefData; sources: Source[] }) {
  return (
    <WidgetCard eyebrow="Morning brief">
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
      <Sources sources={sources} />
    </WidgetCard>
  );
}
