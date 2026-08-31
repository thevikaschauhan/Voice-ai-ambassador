import Link from 'next/link'

export default function NotFound() {
  return (
    <main className="mx-auto max-w-[720px] px-6 py-24">
      <h1 className="text-[15px] tracking-[0.16em] text-ink-100 uppercase">Not found</h1>
      <p className="mt-3 text-[13px] leading-relaxed text-ink-400">
        There is no page at this address.
      </p>
      <Link className="mt-6 inline-block text-[13px] text-brass-400" href="/">
        Back to the call surface
      </Link>
    </main>
  )
}
