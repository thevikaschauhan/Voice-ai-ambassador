import { vi } from 'vitest'

// React 19 needs this to run effects inside act() without warning.
globalThis.IS_REACT_ACT_ENVIRONMENT = true

// Server-side modules are tested under the node environment, where there is no
// DOM to shim and no jest-dom matchers to register. Both are jsdom-only.
if (typeof window !== 'undefined') {
  await import('@testing-library/jest-dom/vitest')

  // jsdom implements neither, and both are called by components under test.
  Element.prototype.scrollIntoView = vi.fn()

  // jsdom ships <dialog> without its modal methods, and Astryx's AppShell
  // opens the small-screen nav drawer as a real modal dialog. Without these
  // the drawer throws "dialog.showModal is not a function" and the failure
  // reads like a component defect rather than a jsdom gap.
  if (typeof HTMLDialogElement !== 'undefined') {
    HTMLDialogElement.prototype.showModal ??= function showModal(this: HTMLDialogElement) {
      this.open = true
    }
    HTMLDialogElement.prototype.close ??= function close(this: HTMLDialogElement) {
      this.open = false
    }
  }
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
