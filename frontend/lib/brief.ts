// Client for the public brief permalink (W6.3). Relative `/api/brief/<token>` is
// proxied to the backend via next.config.js. Returns null on miss/expired/error
// so the page can show a friendly "not found" instead of crashing.

export type PublishedBrief = {
  text: string;
  account_id: string | null;
  as_of: string | null;
  generated_at: string | null;
};

export async function fetchBrief(token: string): Promise<PublishedBrief | null> {
  try {
    const res = await fetch(`/api/brief/${encodeURIComponent(token)}`);
    if (!res.ok) return null;
    return (await res.json()) as PublishedBrief;
  } catch {
    return null;
  }
}
