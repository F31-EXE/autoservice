from io import StringIO

import pytest
from django.core.management import call_command

from tests.workorders.helpers import flat_profile, run_import

pytestmark = pytest.mark.django_db


def test_seed_catalog_command():
    out = StringIO()
    call_command("seed_catalog", stdout=out)
    assert "Catalog synced" in out.getvalue()


def test_catalog_coverage_command(station_a):
    run_import(station_a, flat_profile(station_a), "autodealer_flat.csv")
    out = StringIO()
    call_command("catalog_coverage", station=station_a.id, labor_only=True, stdout=out)
    assert "matched: 4 (100.0%)" in out.getvalue()
    out = StringIO()
    call_command("catalog_coverage", station=station_a.id, stdout=out)
    text = out.getvalue()
    assert "масло моторное 5w-40 4л" in text  # unmatched (part) line is listed
    assert "+7" not in text  # no personal data in the report


def test_expire_task_runs():
    from apps.recommendations.tasks import expire_recommendations_task

    assert expire_recommendations_task() == 0
