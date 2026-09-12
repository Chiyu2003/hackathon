"""Confirmation applies to persisted content, not merely to a factor ID."""
from app.domain.models import Case


CONFIRMATION_CONTEXT = (
    'ruleset_id', 'document_id', 'locality', 'land_use', 'valuation_date',
    'subject_name', 'comparable_name', 'subject_address', 'comparable_address',
    'subject_section', 'comparable_section',
)


def invalidate_confirmations(previous: Case | None, proposed: Case) -> Case:
    """Copy the proposal and clear confirmations affected by substantive changes.

    A changed value must be saved before a subsequent request can confirm it.
    Unchanged content can be explicitly confirmed or unconfirmed by the caller.
    """
    saved = proposed.model_copy(deep=True)
    context_changed = previous is None or any(
        getattr(previous, field) != getattr(saved, field) for field in CONFIRMATION_CONTEXT
    )
    old_factors = {factor.id: factor for factor in previous.factors} if previous else {}
    factors_changed = old_factors.keys() != {factor.id for factor in saved.factors}
    for factor in saved.factors:
        old = old_factors.get(factor.id)
        changed = old is None or old.model_dump(exclude={'confirmed'}) != factor.model_dump(exclude={'confirmed'})
        factors_changed |= changed
        if context_changed or changed:
            factor.confirmed = False
    if not saved.document_id or (previous and previous.document_id != saved.document_id):
        saved.total_evidence = {}
    elif previous:
        for field in list(saved.total_evidence):
            if getattr(previous.totals, field) != getattr(saved.totals, field):
                del saved.total_evidence[field]
    if (context_changed or factors_changed or previous.totals != saved.totals
            or previous.total_evidence != saved.total_evidence):
        saved.totals_confirmed = False
    return saved
