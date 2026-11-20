import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import CommandError, call_command

from apps.llm.client import FakeLLMClient, set_client
from apps.llm.models import LLMCall
from apps.recommendations.management.commands.eval_extraction import score

pytestmark = pytest.mark.django_db
GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "extraction.jsonl"


def test_golden_set_is_valid():
    lines = GOLDEN.read_text("utf-8").splitlines()
    assert len(lines) == 20
    from apps.catalog.models import WorkType

    codes = set(WorkType.objects.values_list("code", flat=True))
    for line in lines:
        item = json.loads(line)
        assert {"input", "expected"} <= set(item)
        for exp in item["expected"]:
            assert exp["work_type_code"] in codes


def test_eval_on_mock():
    out = StringIO()
    call_command("eval_extraction", golden=str(GOLDEN), model="mock", stdout=out)
    text = out.getvalue()
    assert "Examples: 20" in text
    assert "presence" in text and "work_type_code" in text
    assert "Cost per WO" in text
    assert not LLMCall.objects.exists()  # the mock is not billed


def test_eval_with_client_records_calls_and_threshold(settings):
    settings.LLM_PRICE_PER_1K_INPUT_RUB = 1
    set_client(FakeLLMClient(lambda m: '{"recommendations": []}'))
    try:
        out = StringIO()
        call_command("eval_extraction", golden=str(GOLDEN), model="some-model", limit=3,
                     stdout=out)  # fmt: skip
        assert LLMCall.objects.count() == 3
        assert LLMCall.objects.filter(station__isnull=True, model="some-model").count() == 3
        assert "Cost per WO: 0.0000" not in out.getvalue()
        with pytest.raises(CommandError):
            # nothing predicted, codes expected → recall 0, precision vacuous 1 → use recall check
            call_command("eval_extraction", golden=str(GOLDEN), model="x", limit=1,
                         min_precision=1.1, stdout=StringIO())  # fmt: skip
    finally:
        set_client(None)


def test_missing_file():
    with pytest.raises(CommandError):
        call_command("eval_extraction", golden="/nope.jsonl", model="mock")


def test_score():
    examples = [
        {"expected": [{"work_type_code": "a"}, {"work_type_code": "b"}]},
        {"expected": []},
        {"expected": [{"work_type_code": "c"}]},
    ]
    predictions = [
        [{"work_type_code": "a"}, {"work_type_code": "x"}],
        [{"work_type_code": "y"}],
        [],
    ]
    m = score(examples, predictions)
    assert (m["presence"].tp, m["presence"].fp, m["presence"].fn) == (1, 1, 1)
    codes = m["work_type_code"]
    assert (codes.tp, codes.fp, codes.fn) == (1, 2, 2)
    assert codes.precision == pytest.approx(1 / 3)
    assert codes.recall == pytest.approx(1 / 3)
