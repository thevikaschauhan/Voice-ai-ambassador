import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  // The demo surface reads data/inventory.json from the repo root at request
  // time (see lib/inventory.ts). Nothing else escapes web/.
  outputFileTracingRoot: new URL('..', import.meta.url).pathname,
}

export default nextConfig
