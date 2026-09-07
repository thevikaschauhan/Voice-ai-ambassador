import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import ts from 'typescript'
import path from 'node:path'
import { OverviewCards } from '@/components/admin/overview-cards'
import { DocumentList } from '@/components/admin/document-list'
import type { DocumentRow } from '@/lib/admin/knowledge'

afterEach(cleanup)

// Synthetic rendering cases, not API-shape fixtures. The wire fixture is the
// real local API capture in admin-real-shapes.test.ts.
function documentRow(id: string, pending: number, status: DocumentRow['status'] = 'draft') {
  return {
    id, revision: 1, title: `Review note ${id}`, source_type: 'paste' as const,
    status, parse_error_code: null, created_at: '2026-09-07T05:00:00Z',
    published_at: null, figures_pending: pending,
  }
}

function panel() {
  return within(screen.getByRole('region', { name: 'Figures awaiting approval' }))
}

describe('figures awaiting approval', () => {
  it('shows a sentence and zero total when no documents exist', () => {
    render(<OverviewCards leads={[]} documents={[]} />)
    expect(panel().getByText('No figures are awaiting approval.')).toBeInTheDocument()
    expect(panel().getByText('0 figures awaiting approval')).toBeInTheDocument()
    expect(panel().queryAllByRole('link')).toHaveLength(0)
  })

  it('links only pending documents, including a published one, and sums figures', () => {
    render(<OverviewCards leads={[]} documents={[
      documentRow('a', 3), documentRow('b', 0), documentRow('c', 1, 'published'),
    ]} />)
    expect(panel().getByText('4 figures awaiting approval')).toBeInTheDocument()
    expect(panel().getAllByRole('link').map((link) => link.getAttribute('href')))
      .toEqual(['/admin/knowledge/a', '/admin/knowledge/c'])
    expect(panel().getByText('3 figures awaiting approval')).toBeInTheDocument()
    expect(panel().getByText('1 figure awaiting approval')).toBeInTheDocument()
  })

  it('does not infer pending figures from draft status', () => {
    render(<OverviewCards leads={[]} documents={[documentRow('a', 0)]} />)
    expect(panel().getByText('No figures are awaiting approval.')).toBeInTheDocument()
  })

  it('totals all documents while limiting the panel to five links', () => {
    render(<OverviewCards leads={[]} documents={
      Array.from({ length: 7 }, (_, i) => documentRow(String(i), 2))
    } />)
    expect(panel().getByText('14 figures awaiting approval')).toBeInTheDocument()
    expect(panel().getAllByRole('link')).toHaveLength(5)
    expect(panel().getByText('and 2 more')).toBeInTheDocument()
  })

  it('puts each row count beside its source, including zero and singular', () => {
    render(<DocumentList rows={[documentRow('a', 3), documentRow('b', 0), documentRow('c', 1)]} />)
    for (const [id, label] of [
      ['a', '3 figures awaiting approval'],
      ['b', '0 figures awaiting approval'],
      ['c', '1 figure awaiting approval'],
    ]) {
      const row = screen.getByRole('link', { name: `Review note ${id}` }).closest('tr')!
      const source = within(row).getByText('Pasted').closest('td')!
      expect(within(source).getByText(label)).toBeInTheDocument()
    }
  })

  it('requires the API count in the document-list type', () => {
    // Inspect the actual TypeScript type so the missing contract is a counted
    // runtime failure at RED, while the test itself still compiles.
    const filename = path.resolve('src/lib/admin/knowledge.ts')
    const program = ts.createProgram([filename], { noEmit: true })
    const checker = program.getTypeChecker()
    const source = program.getSourceFile(filename)!
    const declaration = source.statements.find((node) =>
      ts.isInterfaceDeclaration(node) && node.name.text === 'DocumentRow')!
    const type = checker.getTypeAtLocation(declaration)
    const field = checker.getPropertyOfType(type, 'figures_pending')
    expect(field, 'DocumentRow must carry the API count').toBeDefined()
    if (!field) return
    expect(checker.typeToString(checker.getTypeOfSymbolAtLocation(field, declaration))).toBe('number')
    expect(field.flags & ts.SymbolFlags.Optional).toBe(0)
  })
})
