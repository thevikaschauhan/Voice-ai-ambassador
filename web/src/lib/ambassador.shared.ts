import type { Language } from '@/lib/types'

/**
 * The parts of the ambassador contract the BROWSER may hold.
 *
 * Split from `lib/ambassador.ts` because that file is `server-only` - it reads
 * the repo root - and the client needs the fallback string and the shape. Same
 * split the inventory already uses (`inventory.ts` / `inventory.shared.ts`), so
 * the server-only marker keeps meaning something.
 */

/**
 * What to say when a language has no name yet.
 *
 * Not a blank label and not "Ambassador": the honest general thing, which is
 * the posture the rest of this surface takes towards an absent value.
 */
export const AMBASSADOR_FALLBACK = "Binghatti's AI ambassador"

export type AmbassadorNames = Record<Language, string>
