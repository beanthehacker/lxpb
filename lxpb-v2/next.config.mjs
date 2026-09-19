/** @type {import('next').NextConfig} */
const nextConfig = {
  // Reports are big, self-contained static HTML files served straight out of
  // public/reports/ -- no image optimization or special handling needed.

  async rewrites() {
    const rules = [
      // /levels is a static page (public/levels/index.html) over the levels
      // runtime; the app's proxy.ts gates it like every other route.
      { source: "/levels", destination: "/levels/index.html" },
    ];
    // Local development only: `next dev` does not run Vercel's Python
    // functions, so forward /api/levels to `python levels_runtime/dev_server.py`
    // (set LEVELS_PY_DEV=http://127.0.0.1:8770). On Vercel, api/levels.py
    // serves this path itself and the variable is unset.
    if (process.env.LEVELS_PY_DEV) {
      rules.push({ source: "/api/levels", destination: `${process.env.LEVELS_PY_DEV}/api/levels` });
    }
    return rules;
  },
};

export default nextConfig;
