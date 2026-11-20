import pytest
from django.contrib.auth.models import AnonymousUser
from django.urls import reverse

from apps.accounts.models import Organization, Role, ServiceStation, User
from tests.factories import ServiceStationFactory, UserFactory

pytestmark = pytest.mark.django_db


class TestQuerySetIsolation:
    def test_manager_sees_only_assigned_station(self, station_a, station_b, manager_a):
        other_in_same_org = ServiceStationFactory(organization=station_a.organization)
        visible = set(ServiceStation.objects.for_user(manager_a))
        assert visible == {station_a}
        assert other_in_same_org not in visible
        assert station_b not in visible

    def test_owner_sees_all_org_stations_but_not_foreign(self, station_a, station_b, owner_a):
        sibling = ServiceStationFactory(organization=station_a.organization)
        assert set(ServiceStation.objects.for_user(owner_a)) == {station_a, sibling}

    def test_manager_cannot_reach_foreign_station_even_if_assigned(self, station_a, station_b):
        # misconfigured M2M must not leak data across organizations
        user = UserFactory(organization=station_a.organization, stations=[station_a, station_b])
        assert set(ServiceStation.objects.for_user(user)) == {station_a}

    def test_superadmin_sees_everything(self, station_a, station_b):
        admin = UserFactory(organization=None, role=Role.SUPERADMIN)
        assert set(ServiceStation.objects.for_user(admin)) == {station_a, station_b}
        assert admin.is_staff

    def test_anonymous_and_inactive_see_nothing(self, station_a, manager_a):
        assert not ServiceStation.objects.for_user(AnonymousUser()).exists()
        assert not ServiceStation.objects.for_user(None).exists()
        manager_a.is_active = False
        manager_a.save()
        assert not ServiceStation.objects.for_user(manager_a).exists()

    def test_user_without_org_sees_nothing(self, station_a):
        user = UserFactory(organization=None)
        assert not ServiceStation.objects.for_user(user).exists()

    def test_organizations_and_users_are_scoped(self, station_a, station_b, manager_a):
        UserFactory(organization=station_b.organization)
        assert list(Organization.objects.for_user(manager_a)) == [station_a.organization]
        assert set(User.objects.for_user(manager_a)) == {manager_a}

    def test_non_superadmin_is_not_staff(self, manager_a, owner_a):
        assert not manager_a.is_staff
        assert not owner_a.is_staff


class TestViewIsolation:
    def test_login_required(self, client, station_a):
        for name, kwargs in [
            ("accounts:today", {}),
            ("accounts:station_list", {}),
            ("accounts:station_detail", {"pk": station_a.pk}),
        ]:
            resp = client.get(reverse(name, kwargs=kwargs))
            assert resp.status_code == 302
            assert reverse("accounts:login") in resp["Location"]

    def test_station_list_hides_foreign(self, client, station_a, station_b, manager_a):
        client.force_login(manager_a)
        resp = client.get(reverse("accounts:station_list"))
        assert resp.status_code == 200
        assert "СТО А" in resp.content.decode()
        assert "СТО Б" not in resp.content.decode()

    def test_foreign_station_detail_is_404(self, client, station_a, station_b, manager_a):
        client.force_login(manager_a)
        own = client.get(reverse("accounts:station_detail", args=[station_a.pk]))
        foreign = client.get(reverse("accounts:station_detail", args=[station_b.pk]))
        assert own.status_code == 200
        assert foreign.status_code == 404

    def test_cannot_switch_to_foreign_station(self, client, station_a, station_b, manager_a):
        client.force_login(manager_a)
        resp = client.post(reverse("accounts:switch_station"), {"station_id": station_b.pk})
        assert resp.status_code == 404

    def test_switch_station_ignores_external_next(self, client, station_a, owner_a):
        client.force_login(owner_a)
        resp = client.post(
            reverse("accounts:switch_station"),
            {"station_id": station_a.pk, "next": "https://evil.example.com/"},
        )
        assert resp["Location"] == reverse("accounts:today")

    def test_today_shows_own_station(self, client, station_a, station_b, manager_a):
        client.force_login(manager_a)
        html = client.get(reverse("accounts:today")).content.decode()
        assert "СТО А" in html and "СТО Б" not in html

    def test_admin_closed_for_owner(self, client, owner_a):
        client.force_login(owner_a)
        resp = client.get("/admin/")
        assert resp.status_code == 302

    def test_login_flow(self, client, manager_a):
        resp = client.post(
            reverse("accounts:login"), {"username": manager_a.username, "password": "pass12345"}
        )
        assert resp.status_code == 302
        assert resp["Location"] == reverse("accounts:today")


def test_station_conf_defaults_and_overrides(db):
    station = ServiceStationFactory(settings={"remind_before_days": 7, "unknown_key": 1})
    conf = station.conf
    assert conf["remind_before_days"] == 7
    assert conf["control_group_share"] == 0.12
    assert "unknown_key" not in conf
