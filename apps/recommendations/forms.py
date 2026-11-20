from django import forms

from apps.catalog.models import Urgency, WorkType

from .models import Recommendation


class ReviewForm(forms.ModelForm):
    work_type = forms.ModelChoiceField(
        queryset=WorkType.objects.order_by("category", "name"),
        required=False,
        label="Вид работ",
        empty_label="— нет в справочнике —",
    )
    urgency = forms.ChoiceField(choices=Urgency.choices, label="Срочность")
    due_date = forms.DateField(
        required=False, label="Срок, дата", widget=forms.DateInput(attrs={"type": "date"})
    )
    due_km = forms.IntegerField(required=False, min_value=0, label="Срок, км")

    class Meta:
        model = Recommendation
        fields = ["work_type", "urgency", "due_date", "due_km"]

    def clean(self):
        data = super().clean()
        if not data.get("due_date") and not data.get("due_km"):
            raise forms.ValidationError("Укажите срок: дату или пробег")
        return data
