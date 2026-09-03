'use client'

import { useCallback, useRef, useState } from 'react'
import {
  ACCEPTED_UPLOAD_EXTENSIONS,
  MAX_UPLOAD_BYTES,
} from '@/lib/admin/knowledge'

/**
 * Adding a document: paste a paragraph, or upload a PDF, DOCX or TXT.
 *
 * NO PARSING HAPPENS HERE. `docs/10-` step 2 puts extraction in the Python
 * adapter, which keeps PDF page numbers and DOCX cell order and is where the
 * format libraries live; the web tier hands over bytes or text and shows what
 * came back. A TypeScript PDF parser in this tier would be a second
 * implementation of the one thing whose output the whole figure gate depends on.
 *
 * The size cap is enforced here AND in the API. The API's is the real gate -
 * this one exists so a reviewer is not asked to upload eight megabytes before
 * being told no, and so the limit can be said out loud beside the control.
 */
export function KnowledgeIntake() {
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  /**
   * The chosen file in STATE, not read off the input during render.
   *
   * A ref does not re-render, so computing the submit button's disabled state
   * from `fileRef.current.files` left it stale: choosing a file did not enable
   * the button until some other state happened to change. The ref stays, but
   * only to CLEAR the input, which is the one thing state cannot do.
   */
  const [file, setFile] = useState<File | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const check = useCallback((candidate: File | undefined): boolean => {
    if (candidate === undefined) {
      setFile(null)
      return true
    }
    if (candidate.size > MAX_UPLOAD_BYTES) {
      setProblem(
        `That file is too large: the limit is ${Math.round(MAX_UPLOAD_BYTES / (1024 * 1024))}MB. Split it, or paste the section that matters.`,
      )
      if (fileRef.current !== null) fileRef.current.value = ''
      setFile(null)
      return false
    }
    setProblem(null)
    setFile(candidate)
    return true
  }, [])

  const submit = useCallback(async () => {
    if (file !== null && file.size > MAX_UPLOAD_BYTES) return

    setBusy(true)
    setProblem(null)
    setDone(null)
    try {
      let response: Response
      if (file !== null) {
        // Multipart, so the bytes are not base64-inflated on the way to a
        // service that is going to parse them anyway.
        const form = new FormData()
        form.set('title', title)
        form.set('file', file)
        response = await fetch('/api/admin/knowledge/documents/upload', {
          method: 'POST',
          body: form,
        })
      } else {
        response = await fetch('/api/admin/knowledge/documents', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ source_type: 'paste', title, text }),
        })
      }

      if (!response.ok) {
        const payload = (await response.json().catch(() => ({}))) as { error?: string }
        setProblem(payload.error ?? 'That document was not accepted.')
        return
      }
      setDone('Added. It appears in the list below once parsing finishes.')
      setTitle('')
      setText('')
      setFile(null)
      if (fileRef.current !== null) fileRef.current.value = ''
    } catch {
      setProblem('Could not reach the server.')
    } finally {
      setBusy(false)
    }
  }, [file, text, title])

  const empty = title.trim() === '' || (text.trim() === '' && file === null)

  return (
    <form
      className="flex flex-col gap-4 border border-ink-800 px-5 py-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (busy) return
        void submit()
      }}
    >
      <div className="flex flex-col gap-1.5">
        <label className="text-[11px] tracking-[0.12em] text-ink-400 uppercase" htmlFor="doc-title">
          Title
        </label>
        <input
          id="doc-title"
          type="text"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          className="max-w-[40ch] border border-ink-700 bg-ink-900 px-4 py-2.5 text-[13px] text-ink-100"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label className="text-[11px] tracking-[0.12em] text-ink-400 uppercase" htmlFor="doc-text">
          Paste text
        </label>
        <textarea
          id="doc-text"
          rows={5}
          value={text}
          onChange={(event) => setText(event.target.value)}
          className="max-w-[80ch] border border-ink-700 bg-ink-900 px-4 py-2.5 text-[13px] text-ink-100"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label className="text-[11px] tracking-[0.12em] text-ink-400 uppercase" htmlFor="doc-file">
          Or a file
        </label>
        <input
          id="doc-file"
          ref={fileRef}
          type="file"
          accept={ACCEPTED_UPLOAD_EXTENSIONS}
          onChange={(event) => check(event.target.files?.[0])}
          className="text-[12px] text-ink-300"
        />
        <p className="text-[11px] text-ink-600">
          PDF, DOCX or TXT, up to {Math.round(MAX_UPLOAD_BYTES / (1024 * 1024))}MB. A scanned
          PDF has no extractable text and will fail: OCR is deferred.
        </p>
      </div>

      <button
        type="submit"
        disabled={busy || empty}
        className="w-fit border border-ink-600 px-5 py-2.5 text-[13px] text-ink-100 hover:border-brass-500 hover:text-brass-400 disabled:opacity-40"
      >
        {busy ? 'Adding' : 'Add document'}
      </button>

      {problem !== null ? (
        <p className="border border-warn-500/40 px-4 py-3 text-[12px] text-ink-300" role="status">
          {problem}
        </p>
      ) : null}
      {done !== null ? (
        <p className="text-[12px] text-ink-400" role="status">
          {done}
        </p>
      ) : null}
    </form>
  )
}
