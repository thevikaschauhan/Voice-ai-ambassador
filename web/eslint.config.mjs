import coreWebVitals from 'eslint-config-next/core-web-vitals'
import nextTypescript from 'eslint-config-next/typescript'

/**
 * Flat config, natively.
 *
 * This used to go through `FlatCompat` from `@eslint/eslintrc`, which is the
 * bridge for configs written in the old `extends` format. `eslint-config-next`
 * 16 ships flat configs directly - each entry point exports a `Linter.Config[]`
 * - so the bridge is not just unnecessary now, it FAILS: running a native flat
 * config back through the compatibility layer produced
 *
 *   TypeError: Converting circular structure to JSON
 *     --- property 'react' closes the circle
 *
 * which is a stack trace inside eslintrc rather than anything about this
 * project's rules. Worth recording because the error names neither the config
 * nor the upgrade that caused it.
 */
const config = [
  { ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts'] },
  ...coreWebVitals,
  ...nextTypescript,
]

export default config
