'use client'

import Link from 'next/link'
import { useCallback, useState } from 'react'
import { Button } from '@astryxdesign/core/Button'
import { TextInput } from '@astryxdesign/core/TextInput'

/**
 * The admin shell: sign-in, then a nav with nothing behind it yet.
 *
 * Deliberately empty. The lead list and the knowledge review are
 * `task-p2-web-leads` and `task-p2-web-knowledge`; what this card owes is the
 * door and the frame, and an empty frame that says it is empty is honest where
 * a frame full of placeholder rows would not be - the same rule the demo
 * surface follows about fixtures.
 */

const SECTIONS = [
  {
    id: 'leads',
    href: '/admin/leads',
    title: 'Leads',
    blurb:
      'Every call that finished, with its interest score, the evidence behind it, and the qualify or reject decision history.',
  },
  {
    id: 'knowledge',
    href: '/admin/knowledge',
    title: 'Knowledge',
    blurb:
      'Documents the ambassador may draw on, their chunks, and the per-figure approvals that let a value be spoken.',
  },
] as const

export function AdminShell({
  signedIn,
  configured,
}: {
  signedIn: boolean
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

  if (!signedIn) {
    return (
      <main className="mx-auto flex min-h-screen max-w-[440px] flex-col justify-center gap-6 px-6 py-10">
        <header>
          <h1 className="text-[15px] tracking-[0.16em] text-ink-100 uppercase">Admin</h1>
          <p className="mt-1.5 text-[12px] leading-relaxed text-ink-500">
            Leads and the ambassador&rsquo;s knowledge base.
          </p>
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
          <p className="border border-ink-700 px-5 py-3.5 text-[13px] leading-relaxed text-ink-400">
            This deployment has no admin access configured, so there is nothing to sign
            in to. An operator sets the access code on the service.
          </p>
        )}

        {refusal !== null ? (
          <p className="border border-warn-500/40 px-5 py-3.5 text-[13px] text-ink-300" role="status">
            {refusal}
          </p>
        ) : null}
      </main>
    )
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-[1080px] flex-col gap-8 px-4 py-6 sm:px-6">
      <header className="flex flex-wrap items-baseline justify-between gap-x-8 gap-y-2">
        <div>
          <h1 className="text-[15px] tracking-[0.16em] text-ink-100 uppercase">Admin</h1>
          <p className="mt-1.5 text-[12px] text-ink-500">
            Signed in with the shared access code.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            void fetch('/api/admin/logout', { method: 'POST' }).then(() =>
              window.location.reload(),
            )
          }}
          className="text-[12px] text-ink-400 hover:text-brass-400"
        >
          Sign out
        </button>
      </header>

      <nav className="grid gap-4 sm:grid-cols-2">
        {SECTIONS.map((section) => (
          <section key={section.id} className="border border-ink-800 px-5 py-4">
            <h2 className="text-[13px] tracking-[0.06em] text-ink-100">{section.title}</h2>
            <p className="mt-1.5 text-[12px] leading-relaxed text-ink-500">{section.blurb}</p>
            {section.href === null ? (
              <p className="mt-3 text-[11px] tracking-[0.12em] text-ink-600 uppercase">
                Not built yet
              </p>
            ) : (
              <Link
                className="mt-3 inline-block text-[11px] tracking-[0.12em] text-ink-300 uppercase hover:text-brass-400"
                href={section.href}
              >
                Open
              </Link>
            )}
          </section>
        ))}
      </nav>

      <p className="max-w-[74ch] text-[12px] leading-relaxed text-ink-600">
        Nothing in Knowledge reaches a call until its chunks are scoped and its figures
        approved one occurrence at a time.{' '}
        <Link className="hover:text-brass-400" href="/">
          Demo surface
        </Link>
      </p>
    </main>
  )
}
