"""A consistent document review snapshot, shared by reads and publication."""

import hashlib
import json
from typing import Any

import asyncpg

from ambassador.inventory import load_inventory
from ambassador.knowledge import FigureOccurrence, withhold_unapproved


def inventory_digest() -> str:
    projects = load_inventory()
    return hashlib.sha256(
        json.dumps(
            [p.model_dump(mode="json") for p in projects], sort_keys=True
        ).encode()
    ).hexdigest()


async def read_review(
    connection: asyncpg.Connection, document_id: str
) -> dict[str, Any]:
    row = await connection.fetchrow(
        "SELECT * FROM knowledge_documents WHERE id = $1 ORDER BY revision DESC LIMIT 1",
        document_id,
    )
    if row is None:
        raise LookupError("no such document")
    document = dict(row)
    chunks = await connection.fetch(
        "SELECT * FROM knowledge_chunks WHERE document_id = $1 AND document_revision = $2 ORDER BY ordinal",
        document_id,
        document["revision"],
    )
    figures = await connection.fetch(
        "SELECT * FROM knowledge_figures WHERE document_id = $1 AND document_revision = $2 ORDER BY id",
        document_id,
        document["revision"],
    )
    document["chunks"] = [dict(c) for c in chunks]
    document["figures"] = [dict(f) for f in figures]
    # Bind source, review projections, publication state AND inventory. A stale
    # figure approval is as material as a different source revision.
    payload = {"document": document, "inventory": inventory_digest()}
    document["review_token"] = hashlib.sha256(
        json.dumps(payload, default=str, sort_keys=True).encode()
    ).hexdigest()
    for chunk in document["chunks"]:
        occurrences = [
            FigureOccurrence(
                figure_id=str(f["id"]),
                chunk_id=str(f["chunk_id"]),
                value=float(f["value"]),
                kind=f["kind"],
                currency=f["currency"],
                unit=f["unit"],
                surface=f["surface"],
                source_sentence=f["source_sentence"],
                approved=f["active_approval_id"] is not None,
            )
            for f in figures
            if f["chunk_id"] == chunk["id"]
        ]
        chunk["review_body"], _ = withhold_unapproved(chunk["body"], occurrences)
    return document
