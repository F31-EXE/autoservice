import pytest

from apps.accounts.models import Role
from tests.factories import ServiceStationFactory, UserFactory


@pytest.fixture
def station_a(db):
    return ServiceStationFactory(name="СТО А")


@pytest.fixture
def station_b(db):
    return ServiceStationFactory(name="СТО Б")


@pytest.fixture
def manager_a(station_a):
    return UserFactory(organization=station_a.organization, stations=[station_a])


@pytest.fixture
def owner_a(station_a):
    return UserFactory(organization=station_a.organization, role=Role.OWNER)
