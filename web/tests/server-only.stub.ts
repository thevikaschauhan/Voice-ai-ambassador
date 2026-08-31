/**
 * `server-only` exists to make a build fail when a server module is imported
 * from a client component. Next applies the `react-server` export condition;
 * vitest does not, so the package resolves to its throwing client entry and
 * every server-side test dies on import.
 *
 * Aliased to this empty module in `vitest.config.ts`. The guard still does its
 * real job in `next build`, which is where it matters.
 */
export {}
