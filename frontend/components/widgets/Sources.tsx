import type { Source } from '@/lib/widgets';

export function Sources({ sources }: { sources: Source[] }) {
  if (!sources || sources.length === 0) return null;
  return (
    <div className="mt-3 pt-2.5 border-t border-border text-[10.5px] text-text-3 flex flex-wrap gap-1.5 items-center">
      <span className="font-medium">Sources:</span>
      {sources.map((s, i) => (
        <span
          key={i}
          className="bg-bg border border-border rounded px-2 py-0.5 font-medium text-text-2"
        >
          {s.url ? (
            <a href={s.url} target="_blank" rel="noopener noreferrer" className="hover:text-accent">
              {s.name}
            </a>
          ) : (
            s.name
          )}
        </span>
      ))}
    </div>
  );
}

// Shared safe HTML — allow ONLY the bare tags <strong>, </strong>, <em>, </em>.
// Any tag carrying attributes (e.g. <strong onclick="...">) is stripped wholesale,
// so event-handler XSS cannot ride in on an allowed tag name. Anything else is
// stripped too. Stray angle-brackets in text are then escaped.
//
// Security note: the previous version used `\b` in the lookahead, which let
// `<strong onclick="evil()">` survive because `strong\b` still matched. The `>`
// terminator below requires the tag to be EXACTLY `<strong>` / `<em>` / closers.
export function SafeHtml({ html, className }: { html: string; className?: string }) {
  const cleaned = (html ?? '')
    // Drop any tag that is not exactly <strong> </strong> <em> </em>
    .replace(/<(?!\/?(?:strong|em)>)[^>]*>/gi, '')
    // Escape any leftover lone '<' or '>' so they render as literal text
    .replace(/<(?!\/?(?:strong|em)>)/gi, '&lt;');
  return <span className={className} dangerouslySetInnerHTML={{ __html: cleaned }} />;
}

// Card wrapper used by every widget so paddings, borders, and the eyebrow line are uniform.
export function WidgetCard({
  eyebrow,
  children,
  className = '',
}: {
  eyebrow?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`bg-surface border border-border rounded-2xl p-4 shadow-sm animate-slide-in ${className}`}>
      {eyebrow && (
        <div className="flex items-center gap-1.5 mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-accent">
          <span className="text-[7px]">◆</span>
          {eyebrow}
        </div>
      )}
      {children}
    </div>
  );
}
