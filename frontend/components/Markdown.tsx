// Markdown renderer for the chat "loose chat" bubble (042).
//
// The agent's plain-text replies (the `message` SSE event — clarifications,
// "I can't do X", short explanations) are markdown. Before 042 they were
// rendered as a raw React string, so `**bold**`, `###`, and `| tables |` showed
// as literal characters. This renders them properly.
//
// SECURITY (threat 7): react-markdown is XSS-safe by construction — it does NOT
// render raw HTML in the source (no `rehype-raw` plugin here), so any `<script>`
// / `<img onerror>` in the model output is treated as text, not markup. This is
// exactly the "markdown via a strict, no-raw-HTML renderer" posture SECURITY.md
// calls for. `remark-gfm` adds tables / strikethrough / task-lists / autolinks.
//
// Styling is done with the `components` map (Tailwind classes per element) since
// the repo has no @tailwindcss/typography plugin. Tables get an overflow-x
// wrapper so a wide table doesn't blow out the narrow phone-frame bubble.

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';

const components: Components = {
  p: ({ children }) => <p className="my-1 first:mt-0 last:mb-0 leading-snug">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  h1: ({ children }) => <div className="mt-2 mb-1 text-[15px] font-semibold">{children}</div>,
  h2: ({ children }) => <div className="mt-2 mb-1 text-[14px] font-semibold">{children}</div>,
  h3: ({ children }) => <div className="mt-2 mb-1 text-[13px] font-semibold">{children}</div>,
  ul: ({ children }) => <ul className="my-1 ml-4 list-disc space-y-0.5">{children}</ul>,
  ol: ({ children }) => <ol className="my-1 ml-4 list-decimal space-y-0.5">{children}</ol>,
  li: ({ children }) => <li className="leading-snug">{children}</li>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer"
       className="text-accent underline underline-offset-2">{children}</a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-1 border-l-2 border-border pl-2.5 text-text-2">{children}</blockquote>
  ),
  hr: () => <hr className="my-2 border-border" />,
  code: ({ children }) => (
    <code className="px-1 py-0.5 rounded bg-surface-2 text-[12px] font-mono">{children}</code>
  ),
  pre: ({ children }) => (
    <pre className="my-1.5 p-2.5 rounded-lg bg-surface-2 text-[12px] font-mono overflow-x-auto">{children}</pre>
  ),
  // Tables — wrap in a horizontal scroller so a wide table fits the phone bubble.
  table: ({ children }) => (
    <div className="my-1.5 -mx-1 overflow-x-auto">
      <table className="w-full text-[12.5px] border-collapse">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="text-left text-text-2">{children}</thead>,
  th: ({ children }) => <th className="border border-border px-2 py-1 font-semibold whitespace-nowrap">{children}</th>,
  td: ({ children }) => <td className="border border-border px-2 py-1 align-top">{children}</td>,
};

export function Markdown({ children }: { children: string }) {
  return (
    <div className="text-sm text-text break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
