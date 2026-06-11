/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Allow image domains for any chart screenshots we host on Supabase
  images: {
    remotePatterns: [
      { protocol: 'https', hostname: '*.supabase.co' },
      { protocol: 'https', hostname: '*.tradingview.com' },
    ],
  },
  // Proxy backend during local dev so we can use /api/* without CORS pain
  async rewrites() {
    // 033: tolerate a scheme-less NEXT_PUBLIC_API_URL (e.g. "foo.up.railway.app",
    // easy to paste from a Railway domain). Next requires rewrite destinations to
    // start with `/`, `http://`, or `https://`; a bare host fails `next build` with
    // "Error: Invalid rewrites found". Default to https:// + strip a trailing slash.
    const raw = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
    const backend = (/^https?:\/\//.test(raw) ? raw : `https://${raw}`).replace(/\/+$/, '');
    return [
      { source: '/api/chat', destination: `${backend}/api/chat` },
      { source: '/api/healthz', destination: `${backend}/healthz` },
      { source: '/api/portfolio', destination: `${backend}/api/portfolio` },
      { source: '/api/mock-chart/:path*', destination: `${backend}/api/mock-chart/:path*` },
      // W4 (pivot) — connect/waitlist endpoints.
      { source: '/api/waitlist', destination: `${backend}/api/waitlist` },
      { source: '/api/ibkr/:path*', destination: `${backend}/api/ibkr/:path*` },
      // W6.3 (pivot) — public brief permalink.
      { source: '/api/brief/:token', destination: `${backend}/api/brief/:token` },
    ];
  },
  // W6.6 — security headers on every page (the live /connect + /b/[token] surface).
  // The non-CSP headers are universally safe. The CSP allowlists what the app
  // actually uses (Supabase auth/realtime, PostHog) + the inline/eval Next needs;
  // ⚠️ verify on a Vercel PREVIEW deploy before promoting — a wrong CSP can block
  // sign-in or analytics. If unsure, swap the key to
  // 'Content-Security-Policy-Report-Only' first, watch the console, then enforce.
  async headers() {
    const csp = [
      "default-src 'self'",
      // Next ships inline bootstrap + some libs eval; no nonce setup here.
      "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://*.posthog.com",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob: https://*.supabase.co https://*.tradingview.com",
      "font-src 'self' data:",
      // Supabase REST/auth/realtime + PostHog ingest. /api/* is same-origin (proxied).
      "connect-src 'self' https://*.supabase.co wss://*.supabase.co https://*.posthog.com",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join('; ');
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy', value: 'geolocation=(), microphone=(), camera=()' },
          { key: 'Strict-Transport-Security', value: 'max-age=63072000; includeSubDomains; preload' },
          { key: 'Content-Security-Policy', value: csp },
        ],
      },
    ];
  },
};
module.exports = nextConfig;
