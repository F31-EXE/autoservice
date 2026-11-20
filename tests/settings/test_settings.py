import pytest
from django.urls import reverse

from apps.accounts.models import ServiceStation, User
from apps.common.models import AuditEvent
from tests.factories import ServiceStationFactory, UserFactory

pytestmark = pytest.mark.django_db

URL = "accounts:settings"


def rules_payload(**overrides):
    data = {
        "remind_before_days": 7, "merge_window_days": 30, "max_touches": 2,
        "second_touch_after_days": 3, "send_from": "10:00", "send_to": "19:00",
        "inactive_vehicle_days": 730, "control_group_pct": "15", "attribution_window_days": 45,
        "high_value_call_threshold": 20000, "recommendation_ttl_days": 540,
        "skip_cooldown_days": 30,
    }  # fmt: skip
    data.update(overrides)
    return {"section": "rules", **{f"rules-{k}": v for k, v in data.items()}}


def test_manager_forbidden(client, manager_a):
    client.force_login(manager_a)
    assert client.get(reverse(URL)).status_code == 403
    assert client.get(reverse("accounts:user_create")).status_code == 403


def test_page_shows_preview(client, owner_a, station_a):
    client.force_login(owner_a)
    resp = client.get(reverse(URL))
    assert resp.status_code == 200
    preview = resp.context["preview"]
    assert preview["sms_segments"] <= 2 and "СТОП" in preview["sms"]


def test_rules_saved_and_used(client, owner_a, station_a):
    client.force_login(owner_a)
    resp = client.post(reverse(URL), rules_payload())
    assert resp.status_code == 302
    station_a.refresh_from_db()
    conf = station_a.conf
    assert conf["remind_before_days"] == 7
    assert conf["send_window"] == ["10:00", "19:00"]
    assert conf["control_group_share"] == pytest.approx(0.15)
    event = AuditEvent.objects.get(action="settings.rules")
    assert event.data["changed"]["remind_before_days"] == [14, 7]


@pytest.mark.parametrize("override,error", [
    ({"send_from": "20:00", "send_to": "10:00"}, "раньше окончания"),
    ({"send_from": "06:00"}, "08:00–22:00"),
    ({"control_group_pct": "80"}, "50"),
    ({"max_touches": 0}, "1"),
])  # fmt: skip
def test_rules_validation(client, owner_a, station_a, override, error):
    client.force_login(owner_a)
    resp = client.post(reverse(URL), rules_payload(**override))
    assert resp.status_code == 200 and error in resp.content.decode()


def test_station_form(client, owner_a, station_a):
    client.force_login(owner_a)
    data = {"section": "station", "st-name": "Мотор на Ленина", "st-city": "Екатеринбург",
            "st-timezone": "Asia/Yekaterinburg", "st-sender_name": "СТО Мотор",
            "st-phone": "+73430000000", "st-booking_url": "",
            "st-avg_km_per_day_default": 60}  # fmt: skip
    assert client.post(reverse(URL), data).status_code == 302
    station_a.refresh_from_db()
    assert (station_a.name, station_a.sender_name, station_a.avg_km_per_day_default) == (
        "Мотор на Ленина", "СТО Мотор", 60)  # fmt: skip
    data["st-sender_name"] = "Очень длинное название автосервиса для SMS"
    assert client.post(reverse(URL), data).status_code == 200


def test_templates(client, owner_a, station_a):
    client.force_login(owner_a)
    ok = {"section": "templates",
          "tpl-sms_template": "{{ client_first_name }}, пора на ТО: {{ link }}. СТОП — отписка",
          "tpl-message_template": ""}  # fmt: skip
    assert client.post(reverse(URL), ok).status_code == 302
    station_a.refresh_from_db()
    assert station_a.conf["sms_template"].startswith("{{ client_first_name }}")
    no_stop = {**ok, "tpl-sms_template": "Привет, {{ client_first_name }}"}
    assert "СТОП" in client.post(reverse(URL), no_stop).content.decode()
    broken = {**ok, "tpl-sms_template": "{% if %} СТОП"}
    assert "Ошибка в шаблоне" in client.post(reverse(URL), broken).content.decode()


def test_create_and_manage_users(client, owner_a, station_a):
    other_org_station = ServiceStationFactory()
    client.force_login(owner_a)
    resp = client.post(reverse("accounts:user_create"), {
        "username": "masha", "first_name": "Маша", "email": "", "password": "Pr0per-pass-123",
        "stations": [station_a.pk, other_org_station.pk],
    })  # fmt: skip
    assert resp.status_code == 200  # a foreign station is not a valid choice
    resp = client.post(reverse("accounts:user_create"), {
        "username": "masha", "first_name": "Маша", "email": "", "password": "Pr0per-pass-123",
        "stations": [station_a.pk],
    })  # fmt: skip
    assert resp.status_code == 302
    masha = User.objects.get(username="masha")
    assert masha.role == "manager" and masha.organization == station_a.organization
    assert list(masha.stations.all()) == [station_a] and not masha.is_staff
    url = reverse("accounts:user_update", args=[masha.pk])
    client.post(url, {"action": "deactivate"})
    masha.refresh_from_db()
    assert not masha.is_active
    client.post(url, {"action": "stations", "stations": [other_org_station.pk]})
    assert list(masha.stations.all()) == []  # foreign stations are ignored
    assert client.post(url, {"action": "weird"}).status_code == 400
    # weak passwords and duplicates are rejected
    payload = {"username": "masha2", "password": "12345678", "stations": [station_a.pk]}
    weak = client.post(reverse("accounts:user_create"), payload)
    assert weak.status_code == 200 and not User.objects.filter(username="masha2").exists()


def test_owner_cannot_touch_self_or_other_orgs(client, owner_a, station_b):
    stranger = UserFactory(organization=station_b.organization, stations=[station_b])
    client.force_login(owner_a)
    assert client.post(reverse("accounts:user_update", args=[stranger.pk]),
                       {"action": "deactivate"}).status_code == 404  # fmt: skip
    client.post(reverse("accounts:user_update", args=[owner_a.pk]), {"action": "deactivate"})
    owner_a.refresh_from_db()
    assert owner_a.is_active
    stranger.refresh_from_db()
    assert stranger.is_active


def test_settings_of_current_station_only(client, owner_a, station_a, station_b):
    client.force_login(owner_a)
    client.post(reverse(URL), rules_payload(remind_before_days=3))
    assert ServiceStation.objects.get(pk=station_b.pk).conf["remind_before_days"] == 14
