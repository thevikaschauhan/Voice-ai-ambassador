import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import type { DocumentDetail } from '@/lib/admin/knowledge'

vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh: vi.fn() }) }))
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
const doc = {
  id: 'doc-1', revision: 1, title: 'Design guide', source_type: 'paste', status: 'draft',
  parse_error_code: null, created_at: '2026-09-08T00:00:00Z', published_at: null,
  review_token: 'review-token',
  chunks: [0, 1].map((n) => ({
    id: `chunk-${n}`, ordinal: n, heading: 'Design', body: `Passage ${n}.`,
    review_body: `Passage ${n}.`, retrieval_scope: 'admin_only', project_id: null,
    conflict_code: null, page_start: null, page_end: null, figures: [],
  })),
} as DocumentDetail

async function mount(data = doc) {
  const { DocumentReview } = await import('@/components/admin/document-review')
  render(<DocumentReview document={data} projects={[{id: 'binghatti-skyrise', name: 'Binghatti Skyrise'}]} />)
}

it('publishes the whole reviewed document in one request without approving numbers', async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({ status: 'published' }))
  vi.stubGlobal('fetch', fetcher)
  await mount()
  expect(screen.queryByRole('button', { name: 'Save scope' })).toBeNull()
  expect(screen.getAllByRole('heading', { name: 'Design' })).toHaveLength(1)
  await userEvent.click(screen.getByRole('checkbox', { name: /I have reviewed/i }))
  await userEvent.click(screen.getByRole('button', { name: 'Publish reviewed content' }))
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
  const [url, init] = fetcher.mock.calls[0]
  expect(url).toBe('/api/admin/knowledge/documents/doc-1/publish')
  const body = JSON.parse(init.body)
  expect(body.expected_review_token).toBe('review-token')
  expect(body.selections).toEqual(doc.chunks.map(c => ({chunk_id: c.id, action: 'general_knowledge', project_id: null})))
  expect(body).not.toHaveProperty('figures')
  expect(await screen.findByText(/Published.*/)).toBeInTheDocument()
})

it('excludes every overlapping chunk in a section and stops an empty publication', async () => {
  vi.stubGlobal('fetch', vi.fn())
  await mount()
  await userEvent.selectOptions(screen.getByRole('combobox', {name: 'Use Design'}), 'admin_only')
  expect(screen.getByRole('button', { name: 'Publish reviewed content' })).toBeDisabled()
  expect(screen.getByText(/No content is selected/i)).toBeInTheDocument()
})

it('requires project context and reports a stale review without discarding selections', async () => {
  const fetcher = vi.fn().mockResolvedValue(Response.json({detail: 'document review has changed'}, {status: 409}))
  vi.stubGlobal('fetch', fetcher)
  await mount()
  await userEvent.selectOptions(screen.getByRole('combobox', {name: 'Document context'}), 'project_knowledge')
  await userEvent.click(screen.getByRole('checkbox', {name: /I have reviewed/i}))
  await userEvent.click(screen.getByRole('button', {name: 'Publish reviewed content'}))
  expect(fetcher).not.toHaveBeenCalled()
  expect(screen.getByText(/Choose a project/i)).toBeInTheDocument()
  await userEvent.selectOptions(screen.getByRole('combobox', {name: 'Document project'}), 'binghatti-skyrise')
  await userEvent.click(screen.getByRole('checkbox', {name: /I have reviewed/i}))
  await userEvent.click(screen.getByRole('button', {name: 'Publish reviewed content'}))
  expect(await screen.findByText(/Another review changed/i)).toBeInTheDocument()
  expect(screen.getByRole('combobox', {name: 'Document project'})).toHaveValue('binghatti-skyrise')
})
