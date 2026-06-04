"""Read a saved chat compaction summary."""

from __future__ import annotations

from dataclasses import dataclass

from src.domain.chatstore import ChatStore


@dataclass(frozen=True)
class ReadChatCompactionSummaryRequest:
    chat_slug: str
    filename: str


@dataclass(frozen=True)
class ReadChatCompactionSummaryResult:
    chat_slug: str
    filename: str
    summary_path: str
    content: str


class ChatNotFound(ValueError):
    pass


class CompactionSummaryNotFound(ValueError):
    pass


def execute(
    chatstore: ChatStore, req: ReadChatCompactionSummaryRequest
) -> ReadChatCompactionSummaryResult:
    if chatstore.get_chat(req.chat_slug) is None:
        raise ChatNotFound(f"chat not found: {req.chat_slug}")

    summary = chatstore.read_chat_compaction_doc(req.chat_slug, req.filename)
    if summary is None:
        raise CompactionSummaryNotFound(
            f"chat compaction summary not found: {req.filename}"
        )

    return ReadChatCompactionSummaryResult(
        chat_slug=req.chat_slug,
        filename=req.filename,
        summary_path=summary[0],
        content=summary[1],
    )


__all__ = [
    "ChatNotFound",
    "CompactionSummaryNotFound",
    "ReadChatCompactionSummaryRequest",
    "ReadChatCompactionSummaryResult",
    "execute",
]
