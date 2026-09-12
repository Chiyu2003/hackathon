"""Vendor-independent tool turns and validated, bounded function inputs."""
from typing import Literal
from pydantic import Field, StrictInt
from app.application.rag_contracts import Contract, AnswerDraft


class SearchInput(Contract):
    question: str = Field(min_length=1, max_length=1000)


class ReadInput(Contract):
    citation_id: str = Field(min_length=1, max_length=64)
    start: StrictInt = Field(default=0, ge=0)


class RuleInput(Contract):
    rule_id: str = Field(min_length=1, max_length=200)


class EmptyInput(Contract):
    pass


class ToolCall(Contract):
    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100)
    arguments: dict


class AgentTurn(Contract):
    calls: tuple[ToolCall, ...] = Field(default=(), max_length=8)
    answer: AnswerDraft | None = None
    # Opaque adapter-owned message preserves signatures/reasoning blocks across turns.
    continuation: dict = Field(default_factory=dict)


class ToolResult(Contract):
    id: str
    status: Literal['success', 'error']
    data: dict


TOOL_INPUTS = {'search_evidence': SearchInput, 'read_source_page': ReadInput,
               'get_rule': RuleInput, 'review_case': EmptyInput}
TOOL_DESCRIPTIONS = {
    'search_evidence': 'Search uploaded applicable rules documents; you may reformulate the question and search again. Returns citation IDs.',
    'read_source_page': 'Read up to 2000 characters from a previously retrieved citation page; start is a character offset. Returns an exact citation.',
    'get_rule': 'Inspect one configured factor. rule_id must exactly equal a factors[].id (for example width), without a ruleset prefix. This returns configuration, NOT a document citation.',
    'review_case': 'Run the existing deterministic valuation review on the saved case. Use this for arithmetic; never calculate yourself.',
}


def tool_catalog(rule_ids=None):
    catalog = [dict(name=name, description=TOOL_DESCRIPTIONS[name], input_schema=model.model_json_schema())
            for name, model in TOOL_INPUTS.items()]
    if rule_ids is not None:
        rule_tool = next(tool for tool in catalog if tool["name"] == "get_rule")
        rule_tool["input_schema"]["properties"]["rule_id"]["enum"] = list(rule_ids)
    return catalog
