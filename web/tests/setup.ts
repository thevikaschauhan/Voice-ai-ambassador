import { vi } from 'vitest'

// React 19 needs this to run effects inside act() without warning.
globalThis.IS_REACT_ACT_ENVIRONMENT = true

// Server-side modules are tested under the node environment, where there is no
// DOM to shim and no jest-dom matchers to register. Both are jsdom-only.
if (typeof window !== 'undefined') {
  await import('@testing-library/jest-dom/vitest')

  // jsdom implements neither, and both are called by components under test.
  Element.prototype.scrollIntoView = vi.fn()
  if (!window.matchMedia) {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      }),
    })
  }
}

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
