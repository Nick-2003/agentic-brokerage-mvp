import DOMPurify from 'isomorphic-dompurify';
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

// Shared safe HTML for widget rich-text. P5 (032): the sanitizer is now the
// industry-standard **DOMPurify** (isomorphic — works in the browser and during
// any SSR/prerender) instead of a hand-rolled regex. Same strict allowlist as
// before — ONLY the inline emphasis tags the widgets actually use, and NO
// attributes — so e.g. `<strong onclick="…">` can't ride in on an allowed tag,
// and `<script>`/`<img onerror>`/`javascript:` links are dropped. DOMPurify
// handles the malformed-markup / mXSS edge cases a regex can't.
const _SANITIZE = { ALLOWED_TAGS: ['strong', 'em'], ALLOWED_ATTR: [] };

export function SafeHtml({ html, className }: { html: string; className?: string }) {
  const cleaned = DOMPurify.sanitize(html ?? '', _SANITIZE);
  return <span className={className} dangerouslySetInnerHTML={{ __html: cleaned }} />;
}

// 076 — account-venue chip. Tells the reader whether a widget shows their REAL
// IBKR book or a PAPER Alpaca position, so the two can never be confused. The
// label text is copied verbatim from the tool result by the agent; this only
// styles it. Renders nothing when there's no label (e.g. unconnected).
export function AccountBadge({ label }: { label?: string }) {
  if (!label) return null;
  // "Paper"/"Sample"/"Demo" read as not-real → amber; "Real" → muted neutral.
  const isReal = /^real\b/i.test(label);
  const tone = isReal
    ? 'bg-bg border-border text-text-2'
    : 'bg-amber-bg border-amber/30 text-amber';
  return (
    <span
      className={`rounded px-2 py-0.5 text-[9.5px] font-semibold uppercase tracking-[0.1em] border ${tone}`}
    >
      {label}
    </span>
  );
}

// Card wrapper used by every widget so paddings, borders, and the eyebrow line are uniform.
export function WidgetCard({
  eyebrow,
  headerRight,
  children,
  className = '',
}: {
  eyebrow?: string;
  headerRight?: React.ReactNode; // 076 — right-aligned slot on the eyebrow row (e.g. AccountBadge)
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`bg-surface border border-border rounded-2xl p-4 shadow-sm animate-slide-in ${className}`}>
      {(eyebrow || headerRight) && (
        <div className="flex items-center gap-1.5 mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-accent">
          {eyebrow && <span className="text-[7px]">◆</span>}
          {eyebrow}
          {headerRight && <span className="ml-auto normal-case tracking-normal">{headerRight}</span>}
        </div>
      )}
      {children}
    </div>
  );
}
