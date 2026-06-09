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
    const backend = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
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
};
module.exports = nextConfig;
