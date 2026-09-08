"""Validate the explicit publication manifest without I/O or framework imports."""

from collections.abc import Collection, Sequence

from .knowledge import ChunkScope, is_prompt_eligible, review_scope
from .schemas import KnowledgeReviewChunk, KnowledgeScopeSelection


def publication_scopes(
    chunks: Sequence[KnowledgeReviewChunk],
    selections: Sequence[KnowledgeScopeSelection],
    inventory_project_ids: Collection[str],
) -> list[ChunkScope]:
    selected = {item.chunk_id: item for item in selections}
    if len(selected) != len(selections) or set(selected) != {c.id for c in chunks}:
        raise ValueError("Review every passage exactly once.")
    scopes = []
    for chunk in chunks:
        choice = selected[chunk.id]
        if choice.action in ("general_knowledge", "project_knowledge"):
            if chunk.retrieval_scope == "inventory_governed":
                raise ValueError("Content managed in inventory must remain excluded.")
            if chunk.conflict_code is not None:
                raise ValueError("Resolve or exclude the conflicting passage.")
        action = (
            "inventory_governed"
            if chunk.retrieval_scope == "inventory_governed"
            else choice.action
        )
        scope = review_scope(
            action,
            project_id=choice.project_id,
            inventory_project_ids=inventory_project_ids,
        )
        if scope.conflict_code is not None:
            raise ValueError("Choose an existing inventory project.")
        scopes.append(
            ChunkScope(
                str(chunk.id),
                scope.retrieval_scope,
                scope.project_id,
                chunk.conflict_code,
            )
        )
    if not any(is_prompt_eligible(scope) for scope in scopes):
        raise ValueError("No content is selected for publication.")
    return scopes
