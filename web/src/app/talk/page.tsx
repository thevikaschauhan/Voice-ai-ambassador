import { TalkCall } from '@/components/talk-call'

export const dynamic = 'force-dynamic'

/**
 * The client-facing talk page.
 *
 * A server component that renders a client one and passes it NOTHING. That is
 * deliberate: the access code is verified in `api/talk`, on the server, and
 * there is no prop this page could pass about it that would not be a fact about
 * a credential leaking into a bundle. The page does not even say whether a code
 * is configured - an unset gate refuses exactly like a wrong code does, and the
 * difference goes to the server log where an operator can act on it.
 */
export default function TalkPage() {
  return <TalkCall />
}
