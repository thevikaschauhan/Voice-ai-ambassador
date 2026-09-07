'use client'

import { useCallback, useState } from 'react'
import { Banner } from '@astryxdesign/core/Banner'
import { Button } from '@astryxdesign/core/Button'
import { Text } from '@astryxdesign/core/Text'
import { TextInput } from '@astryxdesign/core/TextInput'

/**
 * The admin door, and nothing else.
 *
 * IT USED TO BE THE FRAME TOO - a `signedIn` branch with its own header, Sign
 * out and two nav cards. AdminAppShell and the overview cards replaced all of
 * it, and /admin's only call site has passed `signedIn={false}` ever since, so
 * that half was unreachable code still carrying a second copy of the site
 * navigation. Orphaned by task-web-admin-astryx-shell PR A and removed here;
 * the prop went with it, because a boolean with one possible value is a
 * question nobody is asking.
 */

export function AdminShell({
  configured,
}: {
  /** Whether this deployment has an admin code at all. */
  configured: boolean
}) {
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  const signIn = useCallback(async () => {
    // Answered HERE rather than by disabling the control, because a disabled
    // button takes away the one thing a visitor can press to find out what is
    // wrong - and on this page there is nothing else to reason from. Pressing
    // it is the question; this is the answer.
    //
    // It says what to do and NOTHING about what was typed. The value is a
    // secret, so a message reporting its length or shape would be a worse
    // defect than the silence it replaced.
    if (code.trim() === '') {
      setRefusal('Enter the access code.')
      return
    }
    // An empty code is not a wrong code, so it never reaches the door: sending
    // it would spend one of the rate limiter's attempts on a press that
    // carried nothing.

    setBusy(true)
    setRefusal(null)
    try {
      const response = await fetch('/api/admin/login', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ code }),
        cache: 'no-store',
      })
      if (response.status === 204) {
        // The session is an HttpOnly cookie, so the page has to be re-rendered
        // by the server to see it - there is nothing for the browser to read.
        window.location.reload()
        return
      }
      const payload = (await response.json().catch(() => ({}))) as { error?: string }
      setRefusal(payload.error ?? 'That did not work.')
    } catch {
      setRefusal('Could not reach the server.')
    } finally {
      setBusy(false)
    }
  }, [code])

  return (
    <main className="mx-auto flex min-h-screen max-w-[440px] flex-col justify-center gap-6 px-6 py-10">
      <header className="flex flex-col gap-1.5">
        <Text as="h1" type="display-3">
          Admin
        </Text>
        <Text as="p" display="block" color="secondary">
          Leads and the ambassador&rsquo;s knowledge base.
        </Text>
      </header>

      {configured ? (
        <form
          className="flex flex-col gap-3"
          onSubmit={(event) => {
            event.preventDefault()
            if (busy) return
            void signIn()
          }}
        >
          {/*
            Astryx's own field and button. The LABEL TEXT, the busy wording
            and the button name are unchanged - the existing cases in
            admin-shell.test.tsx assert every one of them, which is what
            makes a restyle safe to do. `isDisabled={busy}` is the
            silent-disabled rule from #150: never disabled for a missing
            input, only while the request is in flight.

            THE `id` IS GONE, and it was measured rather than assumed:
            TextInput generates its own id (`_R_6avivb_` in the build I
            checked) and silently drops one passed in, so `#admin-code` no
            longer exists in the DOM. Nothing depends on it - the tests and
            the label association both go through the accessible name, which
            Astryx wires itself - but do not write a selector against that
            id again.
          */}
          <TextInput
            type="password"
            label="Access code"
            value={code}
            onChange={(next) => setCode(next)}
            // MEASURED GAP in Astryx 0.5.3, not a preference: TextInput
            // spreads `...rest` onto the input at runtime, so this reaches
            // the DOM, but `TextInputProps` does not declare it. Dropping it
            // instead would silently cost a password manager the ability to
            // fill this field, which is a real regression on a sign-in
            // screen; asserting it here keeps the affordance and confines
            // the deviation to one line. DELETE THE CAST once upstream
            // declares the prop - do not widen it to other props.
            {...({ autoComplete: 'current-password' } as { autoComplete: string })}
          />
          <Button
            type="submit"
            label={busy ? 'Checking' : 'Sign in'}
            variant="primary"
            isDisabled={busy}
          />
        </form>
      ) : (
        <Banner
          status="info"
          title="This deployment has no admin access configured, so there is nothing to sign in to. An operator sets the access code on the service."
        />
      )}

      {refusal !== null ? (
        <p role="status">
          <Text as="span">{refusal}</Text>
        </p>
      ) : null}
    </main>
  )
}
