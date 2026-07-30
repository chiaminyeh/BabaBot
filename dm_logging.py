from __future__ import annotations

from typing import Any


def _identity(user: Any) -> str:
    return (
        f"user_id={getattr(user, 'id', 'unknown')} "
        f"display_name={str(getattr(user, 'display_name', getattr(user, 'name', 'unknown')))!r}"
    )


def _attachments(attachments: list[Any] | tuple[Any, ...] | None) -> str:
    if not attachments:
        return "attachments=[]"
    rendered = [
        (
            f"filename={str(getattr(item, 'filename', ''))!r} "
            f"url={str(getattr(item, 'url', ''))!r} "
            f"content_type={getattr(item, 'content_type', None)!r} "
            f"size={getattr(item, 'size', None)!r}"
        )
        for item in attachments
    ]
    return f"attachments=[{'; '.join(rendered)}]"


def format_dm_log(direction: str, message: Any) -> str:
    return (
        f"DM {direction.upper()} {_identity(message.author)} "
        f"content={str(getattr(message, 'content', ''))!r} "
        f"{_attachments(getattr(message, 'attachments', None))}"
    )


def format_outgoing_dm(recipient: Any, content: Any) -> str:
    return f"DM OUT {_identity(recipient)} content={str(content)!r} attachments=[]"
