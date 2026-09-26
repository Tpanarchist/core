"""Finance invariants: exact money, immutable records, and explicit wire identities."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext
from typing import cast

import pytest

from core.identity import Entity, Id, Namespace, Ref
from core.time import WallInstant
from core.value import Kind
from personal_finance.domain.accounts import Account, AccountType
from personal_finance.domain.codec import (
    content_hash,
    decode_content,
    decode_id,
    decode_ref,
    encode_content,
    encode_id,
    encode_ref,
)
from personal_finance.domain.kinds import ACCOUNT, ACTION_DRAFT, JOURNAL_ENTRY
from personal_finance.domain.ledger import (
    Draft,
    EntryContent,
    JournalEntry,
    Posting,
    validate_accounts,
)
from personal_finance.domain.money import Money

DAY = date(2026, 9, 21)
AT = WallInstant(datetime(2026, 9, 21, 12, tzinfo=UTC))
CASH = Account(Id(ACCOUNT, "cash"), "Checking", AccountType.ASSET, "USD", liquid=True)
INCOME = Account(Id(ACCOUNT, "income"), "Salary", AccountType.INCOME, "USD")


def income_entry() -> EntryContent:
    return EntryContent(
        DAY,
        "Salary",
        (Posting(Ref(CASH.id), Money(12345, "USD")), Posting(Ref(INCOME.id), Money(-12345, "USD"))),
        tags=("work",),
    )


@pytest.mark.parametrize(
    ("value", "currency", "minor", "formatted"),
    [
        ("12.34", "USD", 1234, "12.34 USD"),
        ("-0.01", "EUR", -1, "-0.01 EUR"),
        ("123", "JPY", 123, "123 JPY"),
        ("-12.345", "KWD", -12345, "-12.345 KWD"),
        ("0.001", "BHD", 1, "0.001 BHD"),
        ("1.2300", "CHF", 123, "1.23 CHF"),
        ("1e2", "CAD", 10000, "100.00 CAD"),
    ],
)
def test_exact_money_and_explicit_currency_exponents(
    value: str,
    currency: str,
    minor: int,
    formatted: str,
) -> None:
    money = Money.from_decimal(value, currency)
    assert money.minor == minor
    assert money.format() == formatted
    assert Money.from_decimal(Decimal(value), currency) == money


@pytest.mark.parametrize("value", ["0.001", "-9.999", "NaN", "Infinity", "-Infinity"])
def test_money_rejects_unrepresentable_values(value: str) -> None:
    with pytest.raises(ValueError):
        Money.from_decimal(value, "USD")


@pytest.mark.parametrize("currency", ["usd", "XXX", "", "USD "])
def test_money_rejects_unsupported_currency(currency: str) -> None:
    with pytest.raises(ValueError):
        Money(0, currency)


@pytest.mark.parametrize("value", [True, 1.25, 3])
def test_money_conversion_never_accepts_float_bool_or_implicit_integer(value: object) -> None:
    with pytest.raises(TypeError):
        Money.from_decimal(cast(str, value), "USD")


def test_money_range_and_arithmetic_are_exact_and_reversible() -> None:
    maximum = Money(2**63 - 1, "USD")
    negative = -maximum
    assert -negative == maximum
    assert Money.from_decimal("92233720368547758.07", "USD") == maximum
    assert maximum + (-maximum) == Money(0, "USD")
    with pytest.raises(ValueError):
        _ = maximum + Money(1, "USD")
    with pytest.raises(ValueError):
        Money(-(2**63), "USD")
    with pytest.raises(ValueError):
        Money.from_decimal("92233720368547758.08", "USD")
    with pytest.raises(ValueError):
        _ = Money(100, "USD") + Money(100, "EUR")
    with pytest.raises(TypeError):
        Money(True, "USD")


def test_decimal_context_cannot_round_financial_input() -> None:
    with localcontext() as context:
        context.prec = 2
        assert Money.from_decimal("123456789.12", "USD").minor == 12345678912
        assert Money(-12345678912, "USD").decimal_string() == "-123456789.12"


@pytest.mark.parametrize(
    ("account_type", "sign"),
    [
        (AccountType.ASSET, 1),
        (AccountType.EXPENSE, 1),
        (AccountType.LIABILITY, -1),
        (AccountType.EQUITY, -1),
        (AccountType.INCOME, -1),
    ],
)
def test_account_sign_comes_from_accounting_type(account_type: AccountType, sign: int) -> None:
    assert Account(Id(ACCOUNT, "a"), "Account", account_type, "USD").display_sign == sign


def test_accounts_carry_core_identity_and_enforce_finance_meaning() -> None:
    entity: Entity = CASH
    assert entity.id == Id(ACCOUNT, "cash")
    with pytest.raises(ValueError):
        Account(Id(JOURNAL_ENTRY, "a"), "Cash", AccountType.ASSET, "USD")
    with pytest.raises(ValueError):
        Account(Id(ACCOUNT, "a"), "  ", AccountType.ASSET, "USD")
    with pytest.raises(ValueError):
        Account(Id(ACCOUNT, "a"), "Debt", AccountType.LIABILITY, "USD", liquid=True)


def test_entry_postings_and_tags_snapshot_caller_owned_sequences() -> None:
    original = income_entry()
    postings = list(original.postings)
    tags = ["work"]
    content = EntryContent(
        DAY, "Salary", cast(tuple[Posting, ...], postings), tags=cast(tuple[str, ...], tags)
    )
    postings.clear()
    tags.append("changed")
    assert len(content.postings) == 2
    assert content.tags == ("work",)
    with pytest.raises(FrozenInstanceError):
        setattr(content, "description", "changed")  # noqa: B010 - test frozen runtime protection


def test_posting_snapshots_nested_reference_namespace() -> None:
    segments = ["book", "personal"]
    reference = Ref(CASH.id, Namespace(cast(tuple[str, ...], segments)))
    posting = Posting(reference, Money(1, "USD"))
    segments.append("changed")
    assert posting.account.namespace == Namespace(("book", "personal"))


def test_entry_rejects_unbalanced_zero_single_and_mixed_currency_postings() -> None:
    entry = income_entry()
    invalid_sets = (
        (entry.postings[0],),
        (entry.postings[0], Posting(Ref(INCOME.id), Money(-12344, "USD"))),
        (Posting(Ref(CASH.id), Money(0, "USD")), Posting(Ref(INCOME.id), Money(0, "USD"))),
        (entry.postings[0], Posting(Ref(INCOME.id), Money(-12345, "EUR"))),
    )
    for postings in invalid_sets:
        with pytest.raises(ValueError):
            replace(entry, postings=postings)


def test_split_entry_balances_with_unbounded_intermediate_integer_sum() -> None:
    maximum = 2**63 - 1
    entry = EntryContent(
        DAY,
        "Large split",
        (
            Posting(Ref(CASH.id), Money(maximum, "USD")),
            Posting(Ref(CASH.id), Money(1, "USD")),
            Posting(Ref(INCOME.id), Money(-maximum, "USD")),
            Posting(Ref(INCOME.id), Money(-1, "USD")),
        ),
    )
    assert len(entry.postings) == 4


def test_entry_account_validation_rejects_unknown_and_wrong_currency_accounts() -> None:
    content = income_entry()
    validate_accounts(content, (CASH, INCOME))
    with pytest.raises(ValueError):
        validate_accounts(content, (CASH,))
    with pytest.raises(ValueError):
        validate_accounts(content, (replace(CASH, currency="EUR"), INCOME))
    with pytest.raises(ValueError):
        validate_accounts(content, (CASH, CASH, INCOME))


def test_entry_distinguishes_financial_date_recorded_time_and_authority() -> None:
    content = income_entry()
    journal = JournalEntry(Id(JOURNAL_ENTRY, "e1"), content, AT, "local-human", 1)
    assert journal.content.effective_date == DAY
    assert journal.recorded_at == AT
    with pytest.raises(ValueError):
        replace(journal, id=Id(ACCOUNT, "e1"))
    with pytest.raises(ValueError):
        replace(journal, principal="")
    with pytest.raises(ValueError):
        replace(journal, sequence=0)
    with pytest.raises(TypeError):
        replace(content, effective_date=cast(date, AT.value))
    with pytest.raises(ValueError):
        replace(content, reversal_of=Ref(CASH.id))


def test_draft_is_immutable_and_hash_binds_reviewed_content() -> None:
    content = income_entry()
    draft = Draft(Id(ACTION_DRAFT, "d1"), content, AT, content_hash(content))
    assert draft.status == "pending"
    with pytest.raises(ValueError):
        replace(draft, content_hash="not-a-hash")
    with pytest.raises(ValueError):
        replace(draft, status="imaginary")


def test_codec_preserves_full_identity_namespace_and_reversal() -> None:
    account_ref = Ref(CASH.id, Namespace(("profiles", "personal")))
    content = replace(
        income_entry(),
        postings=(
            Posting(account_ref, Money(12345, "USD")),
            income_entry().postings[1],
        ),
        reversal_of=Ref(Id(JOURNAL_ENTRY, "e0"), Namespace(("book",))),
    )
    assert decode_content(encode_content(content)) == content
    assert decode_id(encode_id(CASH.id)) == CASH.id
    assert decode_ref(encode_ref(account_ref)) == account_ref
    assert decode_ref(encode_ref(Ref(CASH.id))) == Ref(CASH.id)
    assert decode_id(encode_id(Id(Kind("other.account"), "cash"))) != CASH.id


def test_hash_is_stable_but_changes_with_material_content_or_reference_scope() -> None:
    content = income_entry()
    assert content_hash(decode_content(encode_content(content))) == content_hash(content)
    changed = replace(content, description="Other salary")
    assert content_hash(changed) != content_hash(content)
    scoped = replace(
        content,
        postings=(
            Posting(Ref(CASH.id, Namespace(("other",))), content.postings[0].money),
            content.postings[1],
        ),
    )
    assert content_hash(scoped) != content_hash(content)


@pytest.mark.parametrize(
    "raw",
    [
        '{"version":2,"kind":"finance.account","value":"x"}',
        '{"version":true,"kind":"finance.account","value":"x"}',
        '{"version":1,"kind":"finance.account","value":3}',
        '{"version":1,"kind":"finance.account","value":"x","extra":1}',
        '{"version":1,"kind":"finance.account","value":"x","value":"y"}',
        "[]",
    ],
)
def test_identity_codec_rejects_ambiguous_or_unknown_wire_schema(raw: str) -> None:
    with pytest.raises(ValueError):
        decode_id(raw)


def test_content_codec_revalidates_accounting_and_rejects_boolean_minor_units() -> None:
    encoded = encode_content(income_entry())
    malformed = encoded.replace('"minor":12345', '"minor":true')
    assert malformed != encoded
    with pytest.raises((TypeError, ValueError)):
        decode_content(malformed)
    unbalanced = encoded.replace('"minor":12345', '"minor":12346')
    with pytest.raises(ValueError):
        decode_content(unbalanced)
    assert json.loads(encoded)["version"] == 1
