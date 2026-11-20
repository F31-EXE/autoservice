import re

import pytest

from apps.catalog.matching import CompiledAlias, WorkTypeMatcher, normalize_name
from apps.catalog.models import WorkType, WorkTypeAlias
from apps.catalog.seed_data import WORK_TYPES, expand
from tests.catalog.match_cases import CASES
from tests.factories import ServiceStationFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(scope="module")
def global_matcher(django_db_blocker):
    with django_db_blocker.unblock():
        return WorkTypeMatcher(None)


@pytest.mark.parametrize("name,expected", CASES)
def test_global_catalog_matching(global_matcher, name, expected):
    wt = global_matcher.match(name)
    assert (wt.code if wt else None) == expected


def test_seed_catalog_is_complete():
    assert WorkType.objects.count() == len(WORK_TYPES) >= 80
    for wt in WorkType.objects.all():
        assert wt.aliases.filter(station__isnull=True).exists(), wt.code
        if wt.is_regular:
            assert wt.default_interval_km or wt.default_interval_months, wt.code
    for *_, aliases in WORK_TYPES:
        for raw in aliases:
            kind, pattern = expand(raw)
            if kind == "regex":
                re.compile(pattern)
            else:
                assert "{" not in pattern


def test_seed_is_idempotent():
    from apps.catalog.seed_data import load

    aliases = WorkTypeAlias.objects.count()
    load()
    assert WorkTypeAlias.objects.count() == aliases


def test_keyword_syntax():
    def score(pattern, name):
        alias = WorkTypeAlias(work_type_id=1, pattern=pattern)
        return CompiledAlias.build(alias).score(normalize_name(name))

    assert score("замен колодк", "Замена колодок") == 0  # fleeting vowel: needs "колод"
    assert score("замен колод", "Замена колодок") > 0
    assert score("замен колод -задн", "Замена задних колодок") == 0
    assert score("водяного_насоса", "замена водяного насоса") > 0
    assert score("а|абв", "абв") == 3  # the longest matching alternative counts


def test_station_alias_overrides_global():
    station = ServiceStationFactory()
    oil = WorkType.objects.get(code="engine_oil")
    WorkTypeAlias.objects.create(work_type=oil, station=station, pattern="экспресс_то")
    WorkTypeAlias.objects.create(
        work_type=WorkType.objects.get(code="cabin_filter"),
        station=station,
        kind="regex",
        pattern="масл\\w+ двс",
    )
    m = WorkTypeMatcher(station.id)
    assert m.match("Экспресс ТО").code == "engine_oil"
    assert m.match("Замена масла ДВС").code == "cabin_filter"  # station alias wins
    # other stations are unaffected
    assert WorkTypeMatcher(None).match("Экспресс ТО") is None
    assert (
        WorkTypeMatcher(ServiceStationFactory().id).match("Замена масла ДВС").code == "engine_oil"
    )


def test_priority_beats_specificity():
    station = ServiceStationFactory()
    WorkTypeAlias.objects.create(
        work_type=WorkType.objects.get(code="engine_oil"), station=station, pattern="а", priority=5
    )
    WorkTypeAlias.objects.create(
        work_type=WorkType.objects.get(code="coolant"), station=station, pattern="антифриз"
    )
    assert WorkTypeMatcher(station.id).match("замена антифриза").code == "engine_oil"


def test_alias_validation():
    from django.core.exceptions import ValidationError

    wt = WorkType.objects.first()
    with pytest.raises(ValidationError):
        WorkTypeAlias(work_type=wt, kind="regex", pattern="(").clean()
    with pytest.raises(ValidationError):
        WorkTypeAlias(work_type=wt, pattern="-только -минусы").clean()
