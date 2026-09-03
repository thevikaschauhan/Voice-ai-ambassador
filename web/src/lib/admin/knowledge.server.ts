import 'server-only'

import { readForPage } from '@/lib/admin/read'
import type { PageRead } from '@/lib/admin/read'
import type {
  DocumentDetail,
  DocumentRow,
  KnowledgeChunkView,
  KnowledgeFigureView,
} from '@/lib/admin/knowledge'

/**
 * Reading knowledge from the admin API, in the shapes it actually sends.
 *
 * `get_document` returns chunks and figures SIDE BY SIDE on the document, with
 * the figures ordered by `chunk_id` (one query, `repository.get_figures`). The
 * review UI needs them grouped by chunk, because a chunk's scope is what
 * decides whether an approved figure could ever be spoken - so the grouping
 * happens here rather than being asked of the route. God's call, and it is the
 * cheaper side to change.
 *
 * A figure whose `chunk_id` matches no chunk is kept and surfaced rather than
 * dropped: an unreviewed figure is unspeakable, so losing one is SAFE but
 * SILENT, and silent is what turns it into a bug the next time these shapes
 * move.
 */

interface UpstreamFigure extends KnowledgeFigureView {
  chunk_id: string
}

interface UpstreamDocument extends DocumentRow {
  chunks?: Omit<KnowledgeChunkView, 'figures'>[]
  figures?: UpstreamFigure[]
}

export interface DocumentDetailView extends DocumentDetail {
  /** Figures that grouped nowhere. Empty in the healthy case. */
  orphanFigures: KnowledgeFigureView[]
}

export async function readDocumentRows(
  request: Request,
): Promise<PageRead<DocumentRow[]>> {
  // A bare array, not `{documents: [...]}`.
  return readForPage<DocumentRow[]>(request.headers.get('cookie'), {
    route: 'documents',
  })
}

export async function readDocument(
  request: Request,
  id: string,
): Promise<PageRead<DocumentDetailView>> {
  const read = await readForPage<UpstreamDocument>(request.headers.get('cookie'), {
    route: 'document',
    id,
  })
  if (read.state !== 'ok') return read

  const upstream = read.data
  const byChunk = new Map<string, KnowledgeFigureView[]>()
  for (const chunk of upstream.chunks ?? []) byChunk.set(chunk.id, [])
  const orphans: KnowledgeFigureView[] = []

  for (const figure of upstream.figures ?? []) {
    const bucket = byChunk.get(figure.chunk_id)
    if (bucket === undefined) {
      orphans.push(figure)
      continue
    }
    bucket.push(figure)
  }

  return {
    state: 'ok',
    data: {
      ...upstream,
      chunks: (upstream.chunks ?? []).map((chunk) => ({
        ...chunk,
        figures: byChunk.get(chunk.id) ?? [],
      })),
      orphanFigures: orphans,
    },
  }
}
