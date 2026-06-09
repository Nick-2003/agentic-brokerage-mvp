import type { Metadata } from 'next';

// The brief contains the user's holdings/P&L — NEVER let search engines index it.
// (The token-gated URL is a capability link, not a public page.)
export const metadata: Metadata = {
  title: 'Your portfolio briefing',
  robots: { index: false, follow: false },
};

export default function BriefLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
