"""Forms for screen 8 "Настройки СТО" (owner)."""

from __future__ import annotations

from datetime import time
from zoneinfo import available_timezones

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.template import Template, TemplateSyntaxError

from .models import Role, ServiceStation, User

RU_TIMEZONES = sorted(
    tz for tz in available_timezones()
    if tz.startswith(("Europe/Moscow", "Europe/Kaliningrad", "Europe/Samara", "Europe/Volgograd",
                      "Europe/Saratov", "Europe/Ulyanovsk", "Europe/Astrakhan", "Europe/Kirov",
                      "Asia/Yekaterinburg", "Asia/Omsk", "Asia/Novosibirsk", "Asia/Barnaul",
                      "Asia/Tomsk", "Asia/Novokuznetsk", "Asia/Krasnoyarsk", "Asia/Irkutsk",
                      "Asia/Chita", "Asia/Yakutsk", "Asia/Khandyga", "Asia/Vladivostok",
                      "Asia/Ust-Nera", "Asia/Magadan", "Asia/Sakhalin", "Asia/Srednekolymsk",
                      "Asia/Kamchatka", "Asia/Anadyr"))
)  # fmt: skip


class StationForm(forms.ModelForm):
    timezone = forms.ChoiceField(label="Часовой пояс", choices=[(t, t) for t in RU_TIMEZONES])
    booking_url = forms.URLField(label="Ссылка на запись", required=False, assume_scheme="https")

    class Meta:
        model = ServiceStation
        fields = ["name", "city", "timezone", "sender_name", "phone", "booking_url",
                  "avg_km_per_day_default"]  # fmt: skip
        help_texts = {
            "sender_name": "Как СТО подписывается в сообщениях, коротко: «СТО Мотор».",
            "booking_url": "Если пусто и бот MAX не настроен — в SMS будет телефон.",
            "avg_km_per_day_default": "Используется, пока у машины мало визитов для оценки.",
        }

    def clean_sender_name(self):
        value = self.cleaned_data["sender_name"].strip()
        if len(value) > 30:
            raise forms.ValidationError("Не длиннее 30 символов — иначе SMS не поместится.")
        return value

    def clean_avg_km_per_day_default(self):
        value = self.cleaned_data["avg_km_per_day_default"]
        if not 5 <= value <= 300:
            raise forms.ValidationError("От 5 до 300 км в день.")
        return value


def _int(label, lo, hi, help_text=""):
    return forms.IntegerField(label=label, min_value=lo, max_value=hi, help_text=help_text)


class RulesForm(forms.Form):
    """Spec 7.3 settings stored in ServiceStation.settings."""

    remind_before_days = _int("За сколько дней до срока писать", 0, 90)
    merge_window_days = _int("Объединять рекомендации машины в окне, дней", 0, 180)
    max_touches = _int("Максимум сообщений по одному напоминанию", 1, 5)
    second_touch_after_days = _int("Повторить, если нет ответа, через дней", 1, 30)
    send_from = forms.TimeField(
        label="Отправлять с", widget=forms.TimeInput(attrs={"type": "time"})
    )
    send_to = forms.TimeField(label="Отправлять до", widget=forms.TimeInput(attrs={"type": "time"}))
    inactive_vehicle_days = _int("Не писать, если нет визитов дольше, дней", 90, 3650)
    control_group_pct = forms.DecimalField(
        label="Контрольная группа, %", min_value=0, max_value=50, decimal_places=1,
        help_text="Этим клиентам не пишем — так видно реальный эффект рассылки. "
        "Рекомендуем 10–15%.",
    )  # fmt: skip
    attribution_window_days = _int("Окно атрибуции визита, дней", 7, 120)
    high_value_call_threshold = _int("Выше этой суммы — звонок вместо SMS, ₽", 0, 10_000_000)
    recommendation_ttl_days = _int("Срок жизни рекомендации, дней", 30, 3650)
    skip_cooldown_days = _int("Пауза после пропуска / без ответа, дней", 0, 365)

    FIELDS = ["remind_before_days", "merge_window_days", "max_touches", "second_touch_after_days",
              "inactive_vehicle_days", "attribution_window_days", "high_value_call_threshold",
              "recommendation_ttl_days", "skip_cooldown_days"]  # fmt: skip

    @classmethod
    def initial_from(cls, conf: dict) -> dict:
        start, end = conf["send_window"]
        initial = {k: conf[k] for k in cls.FIELDS}
        share = round(float(conf["control_group_share"]) * 100, 1)
        initial.update(send_from=start, send_to=end, control_group_pct=share)
        return initial

    def clean(self):
        data = super().clean()
        start, end = data.get("send_from"), data.get("send_to")
        if start and end and start >= end:
            raise forms.ValidationError("Время начала отправки должно быть раньше окончания.")
        if start and (start < time(8, 0) or (end and end > time(22, 0))):
            raise forms.ValidationError("Отправляйте сообщения в пределах 08:00–22:00.")
        return data

    def to_settings(self) -> dict:
        d = self.cleaned_data
        values = {k: d[k] for k in self.FIELDS}
        values["send_window"] = [d["send_from"].strftime("%H:%M"), d["send_to"].strftime("%H:%M")]
        values["control_group_share"] = float(d["control_group_pct"]) / 100
        return values


class TemplatesForm(forms.Form):
    sms_template = forms.CharField(
        label="Шаблон SMS", required=False, widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Пусто — встроенный короткий шаблон. Обязательно «СТОП» для отписки.",
    )  # fmt: skip
    message_template = forms.CharField(
        label="Шаблон сообщения в MAX", required=False, widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Пусто — встроенный шаблон. Кнопки «Записаться» и др. добавляются сами.",
    )  # fmt: skip

    def _check(self, name: str) -> str:
        value = self.cleaned_data[name].strip()
        if not value:
            return ""
        try:
            Template(value)
        except TemplateSyntaxError as exc:
            raise forms.ValidationError(f"Ошибка в шаблоне: {exc}") from exc
        if "СТОП" not in value.upper():
            raise forms.ValidationError(
                "В тексте должна быть инструкция для отписки со словом СТОП."
            )
        return value

    def clean_sms_template(self):
        return self._check("sms_template")

    def clean_message_template(self):
        return self._check("message_template")


class NewUserForm(forms.Form):
    username = forms.CharField(label="Логин", max_length=150)
    first_name = forms.CharField(label="Имя", max_length=150, required=False)
    email = forms.EmailField(label="E-mail", required=False)
    password = forms.CharField(label="Пароль", widget=forms.PasswordInput, min_length=8)
    stations = forms.ModelMultipleChoiceField(
        label="Доступ к точкам", queryset=ServiceStation.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )  # fmt: skip

    def __init__(self, *args, stations=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stations"].queryset = stations

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Такой логин уже есть.")
        return username

    def clean(self):
        data = super().clean()
        if data.get("password"):
            user = User(username=data.get("username", ""), email=data.get("email", ""))
            try:
                validate_password(data["password"], user)
            except forms.ValidationError as exc:
                self.add_error("password", exc)
        return data


ROLE_CHOICES = [(Role.MANAGER, Role.MANAGER.label)]
