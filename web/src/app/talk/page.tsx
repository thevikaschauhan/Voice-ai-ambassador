import { TalkCall } from '@/components/talk-call'
import { loadAmbassadorNames } from '@/lib/ambassador'

export const dynamic = 'force-dynamic'

/**
 * The client-facing talk page.
 *
 * A server component that renders a client one. It passes NO secret and no
 * fact about one: the access code is verified in `api/talk`, on the server, and
 * this page does not even say whether a code is configured - an unset gate
 * refuses exactly like a wrong one, and the difference goes to the server log.
 *
 * It does pass the ambassadors' NAMES, all three of them, read at request time
 * from `data/ambassadors.yaml` the same way the inventory is read. All three
 * rather than one, because the visitor has not picked a language yet when this
 * renders, and a name is not a secret - it is on the screen a moment later.
 */
export default async function TalkPage() {
  const names = await loadAmbassadorNames()
  return <TalkCall names={names} />
}
