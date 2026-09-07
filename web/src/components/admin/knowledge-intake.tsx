'use client'

import { useCallback, useRef, useState } from 'react'
import { Button } from '@astryxdesign/core/Button'
import { Field } from '@astryxdesign/core/Field'
import { Text } from '@astryxdesign/core/Text'
import { TextArea } from '@astryxdesign/core/TextArea'
import { TextInput } from '@astryxdesign/core/TextInput'
import {
  ACCEPTED_UPLOAD_EXTENSIONS,
  MAX_UPLOAD_BYTES,
} from '@/lib/admin/knowledge'

/** The longest a derived title may be; the API caps `title` at 300. */
const DERIVED_TITLE_MAX = 120

/**
 * A title for a document the reviewer did not name.
 *
 * There is always one available, which is why a missing Title is never a
 * reason to refuse: an upload is named after its file and a paste after its
 * first line. Both are what the reviewer would have typed, so deriving it
 * removes a required field rather than inventing a fact.
 */
/**
 * A file size a person can judge, beside its name.
 *
 * A reviewer who picked the wrong file usually knows it from the size, which
 * is the one fact the browser's own "No file chosen" chrome gave them and the
 * hidden input no longer can. Whole units and no decimals: this is a sanity
 * check on a 12MB cap, not a measurement.
 */
function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${Math.round(bytes / (1024 * 1024))} MB`
}

function derivedTitle(file: File | null, text: string): string {
  if (file !== null) {
    // The extension is how the file is stored, not what the document is
    // called. Only a trailing one goes: "Q4 2026. plan.pdf" keeps its dots.
    return file.name.replace(/\.[^.]+$/, '').trim().slice(0, DERIVED_TITLE_MAX)
  }
  const firstLine = text.split(/\r?\n/).find((line) => line.trim() !== '') ?? ''
  return firstLine.trim().slice(0, DERIVED_TITLE_MAX)
}

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
 *
 * THE SUBMIT CONTROL IS ALWAYS PRESSABLE except while a request is in flight.
 * It used to disable itself whenever a field was empty, and a reviewer who
 * pasted a paragraph without typing a Title saw a faded grey label, no reason,
 * and no request when they clicked it - which is what the human reported on
 * 2026-09-07 as "there's no CTA to save it". A disabled button takes away the
 * one thing they can press to find out what is wrong. Pressing it is the
 * question; `submit` answers it in the status region.
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
  /**
   * Whether the form is showing (finding G6's intake half).
   *
   * A reviewer opens this page to READ the library far more often than to add
   * to it, and it used to open with a five-row textarea and a full-width
   * submit above the first document. Closed by default; the list is what the
   * page is for.
   */
  const [open, setOpen] = useState(false)
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

    // Checked HERE rather than by disabling the control, because a disabled
    // button takes away the only thing a reviewer can press to find out what
    // is wrong. Pressing it is how they ask; this is the answer.
    if (text.trim() === '' && file === null) {
      setDone(null)
      setProblem('Paste text or choose a file, then press Add document.')
      return
    }

    // Never a reason to refuse: an upload is named after its file and a paste
    // after its first line, so the reviewer always has a title whether or not
    // they typed one.
    const named = title.trim() === '' ? derivedTitle(file, text) : title.trim()

    setBusy(true)
    setProblem(null)
    setDone(null)
    try {
      let response: Response
      if (file !== null) {
        // Multipart, so the bytes are not base64-inflated on the way to a
        // service that is going to parse them anyway.
        const form = new FormData()
        form.set('title', named)
        form.set('file', file)
        response = await fetch('/api/admin/knowledge/documents/upload', {
          method: 'POST',
          body: form,
        })
      } else {
        response = await fetch('/api/admin/knowledge/documents', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ source_type: 'paste', title: named, text }),
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

  return (
    <div className="flex flex-col gap-3">
      {/*
        THE TRIGGER, which reports whether the form is open. `aria-expanded`
        is what tells a screen reader user the form EXISTS and what state it
        is in; a panel that simply appeared would tell them neither.

        "NEW DOCUMENT", NOT "ADD DOCUMENT", and that is a correction rather
        than a preference. Naming the trigger after the submit put TWO buttons
        called "Add document" on the page whenever the panel was open, and a
        screen reader user hearing them in sequence has nothing to choose
        between - the same ambiguity the figure list already solved by naming
        each Approve after its occurrence. The trigger opens a blank form; the
        submit inside performs the add and keeps its own word untouched.
      */}
      <Button
        type="button"
        label="New document"
        variant="secondary"
        aria-expanded={open}
        aria-controls="knowledge-intake-panel"
        onClick={() => setOpen((wasOpen) => !wasOpen)}
      />

      {/*
        UNMOUNTED WHEN CLOSED, not hidden with CSS. A form still in the DOM
        keeps its fields in the tab order and findable by label, so a keyboard
        user would tab into controls that are not on screen - and a test's
        `getByLabelText` would resolve to an invisible input. Unmounting also
        means the draft is discarded on close, which is the honest behaviour
        for a panel whose trigger says "Add document" rather than "Resume".
      */}
      {open ? (
    <form
      id="knowledge-intake-panel"
      className="flex flex-col gap-4 rounded-[var(--radius-container)] border border-[var(--color-border)] p-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (busy) return
        void submit()
      }}
    >
      {/*
        `label="Title (optional)"` rather than Astryx's `isOptional` marker:
        the words on screen stay the ones that were there. The marker is
        Astryx's own wording, and this form's copy is not mine to reword.
      */}
      <TextInput
        label="Title (optional)"
        value={title}
        onChange={(next) => setTitle(next)}
        width="40ch"
      />

      <TextArea
        label="Paste text"
        rows={5}
        value={text}
        onChange={(next) => setText(next)}
        width="80ch"
      />

      {/*
        A NATIVE file input inside Astryx's Field, and the `description` is the
        point of this commit: Field derives `${inputID}-desc` for it and its
        own docs say the consumer wires `aria-describedby`, so the accepted
        formats and the scanned-PDF warning are now the FIELD'S description
        rather than a paragraph next to it. A screen reader user on this input
        used to hear "Or a file" and nothing else.

        Astryx's FileInput is not adopted here: it is controlled by a
        `File | null` value where this form resets the input through a ref
        after a successful add, and the closure's cases pin the native input's
        `accept` string and drive it with `userEvent.upload`. Changing the
        control would change the behaviour on the exact path the human
        reported a bug on in #147.
      */}
      <Field
        label="Or a file"
        inputID="doc-file"
        description={`PDF, DOCX or TXT, up to ${Math.round(
          MAX_UPLOAD_BYTES / (1024 * 1024),
        )}MB. A scanned PDF has no extractable text and will fail: OCR is deferred.`}
      >
        {/*
          THE HUMAN'S REQUEST, 2026-09-07: "Make choose file in the knowledge
          screen as a CTA, currently it's just a text."

          A native file input's button is SHADOW DOM and cannot be themed
          cross-browser, so no amount of CSS on the input was going to make it
          match "Add document". The fix is a wrapper: a real Astryx Button that
          forwards its click to the input, and the input itself taken off the
          screen. `sr-only` and not `display:none` or `hidden` - a display-none
          input cannot be clicked programmatically in every browser, which
          would break the very CTA that now drives it.

          The input STAYS in the DOM with its id, its accept string and its
          ref. See the note above about FileInput: the closure's cases pin that
          accept string and drive this element with `userEvent.upload`, and the
          form resets it through `fileRef.current.value`.
        */}
        {/*
          A DROP ZONE, which is a styled <label> and not a div: the label's
          `htmlFor` makes the WHOLE AREA a click target for the input as well
          as a drop target, with no JavaScript and no second click handler to
          keep in step with the button. The accepted formats stay the FIELD's
          description above - one hint, not two.

          E's Choose file CTA is unchanged inside it. The button is what a
          reviewer presses; the zone is what they can also drop onto or click
          anywhere in.
        */}
        <label
          htmlFor="doc-file"
          data-testid="drop-zone"
          data-file-field
          className="flex flex-wrap items-center gap-3 rounded-[var(--radius-element)] border border-dashed border-[var(--color-border)] px-4 py-3"
        >
          <input
            id="doc-file"
            aria-describedby="doc-file-desc"
            ref={fileRef}
            type="file"
            accept={ACCEPTED_UPLOAD_EXTENSIONS}
            onChange={(event) => check(event.target.files?.[0])}
            className="sr-only"
          />
          <Button
            type="button"
            label="Choose file"
            // SECONDARY, so "Add document" stays the only primary: two
            // primaries on one form is two things claiming to be the next
            // step. This one prepares the submission, it does not make it.
            variant="secondary"
            onClick={() => fileRef.current?.click()}
          />
          {/*
            What the browser's chrome used to say, in our own prose. Hiding the
            input hid "No file chosen" with it, so the chosen file has to be
            named here or a reviewer cannot tell whether the picker took.
          */}
          {file === null ? (
            <Text as="span" type="supporting" color="secondary">
              or drop one here
            </Text>
          ) : (
            <Text as="span" type="supporting" color="secondary">
              {`${file.name}, ${fileSize(file.size)}`}
            </Text>
          )}
        </label>
      </Field>

      <Button
        type="submit"
        label={busy ? 'Adding' : 'Add document'}
        variant="primary"
        /* #147 and #149: never disabled for a missing input. Pressing it with
           nothing filled in is how a reviewer learns what is missing. */
        isDisabled={busy}
      />

      {problem !== null ? (
        <p role="status">
          <Text as="span">{problem}</Text>
        </p>
      ) : null}
      {done !== null ? (
        <p role="status">
          <Text as="span">{done}</Text>
        </p>
      ) : null}
    </form>
      ) : null}
    </div>
  )
}
