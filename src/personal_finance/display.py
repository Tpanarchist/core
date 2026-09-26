"""Safe, complete human-readable presentation of reviewed finance content."""

from __future__ import annotations

from core.identity import Ref
from personal_finance.domain.accounts import Account
from personal_finance.domain.ledger import EntryContent


def safe_display(value: str) -> str:
    """Make controls and formatting characters visible before terminal output.

    Stored content stays unchanged. Escaping here prevents imported C0/C1 and
    bidirectional control characters from hiding or rearranging approval text.
    """
    return "".join(
        character
        if character.isprintable()
        else (
            f"\\u{ord(character):04X}" if ord(character) <= 0xFFFF else f"\\U{ord(character):08X}"
        )
        for character in value
    )


def _reference_text(reference: Ref) -> str:
    identity = f"{safe_display(reference.id.kind.value)}:{safe_display(reference.id.value)}"
    if reference.namespace is None:
        return identity
    namespace = "/".join(safe_display(segment) for segment in reference.namespace.segments)
    return f"{identity} @ {namespace}"


def content_text(content: EntryContent, accounts: tuple[Account, ...]) -> str:
    """Show every field bound by the canonical review hash, including references."""
    names = {account.id: account.name for account in accounts}
    lines = [
        f"Effective date: {content.effective_date}",
        f"Description: {safe_display(content.description)}",
        "",
        "POSTINGS · signed debit / credit",
    ]
    lines.extend(
        f"{safe_display(names.get(posting.account.id, posting.account.id.value))} "
        f"[{_reference_text(posting.account)}]  {posting.money.format()}"
        for posting in content.postings
    )
    lines.extend(
        (
            "",
            "Tags: " + (", ".join(safe_display(tag) for tag in content.tags) or "none"),
            "Source: " + safe_display(content.source),
            "Reverses: "
            + (_reference_text(content.reversal_of) if content.reversal_of else "none"),
        )
    )
    return "\n".join(lines)
