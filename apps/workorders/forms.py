from __future__ import annotations

from django import forms

from .importing.fields import DATE_FORMATS, FIELDS, missing_required
from .models import Layout, SourceSystem

MAX_UPLOAD_MB = 30


class UploadForm(forms.Form):
    file = forms.FileField(label="Файл выгрузки (.xlsx или .csv)")
    source_system = forms.ChoiceField(label="Система-источник", choices=SourceSystem.choices)
    remap = forms.BooleanField(
        label="Настроить сопоставление колонок заново", required=False, initial=False
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith((".xlsx", ".csv")):
            raise forms.ValidationError("Поддерживаются только файлы .xlsx и .csv")
        if f.size > MAX_UPLOAD_MB * 1024 * 1024:
            raise forms.ValidationError(f"Файл больше {MAX_UPLOAD_MB} МБ")
        return f


class MappingForm(forms.Form):
    profile_name = forms.CharField(label="Название профиля", max_length=128)
    layout = forms.ChoiceField(
        label="Формат файла", choices=Layout.choices, widget=forms.RadioSelect
    )
    date_format = forms.ChoiceField(
        label="Формат даты", choices=[(fmt, example) for fmt, example in DATE_FORMATS]
    )

    def __init__(self, *args, headers: list[str], **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", "— не используется —")] + [(h, h) for h in headers]
        self.field_keys = []
        for f in FIELDS:
            name = f"col_{f.key}"
            self.fields[name] = forms.ChoiceField(
                label=f.label + (" *" if f.required else ""), choices=choices, required=False
            )
            self.field_keys.append(f.key)

    def column_fields(self):
        return [self[f"col_{k}"] for k in self.field_keys]

    def column_map(self) -> dict[str, str]:
        return {k: self.cleaned_data.get(f"col_{k}") or "" for k in self.field_keys}

    def clean(self):
        data = super().clean()
        cmap = self.column_map()
        missing = missing_required(cmap)
        if missing:
            raise forms.ValidationError("Не выбраны обязательные поля: " + ", ".join(missing))
        used = [v for v in cmap.values() if v]
        dupes = sorted({v for v in used if used.count(v) > 1})
        if dupes:
            raise forms.ValidationError(
                "Одна колонка выбрана для нескольких полей: " + ", ".join(dupes)
            )
        return data
