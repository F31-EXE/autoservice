from django import forms


class SkipForm(forms.Form):
    reason = forms.CharField(
        label="Причина", max_length=500, widget=forms.TextInput(attrs={"placeholder": "Причина"})
    )


class TextForm(forms.Form):
    text = forms.CharField(label="Текст сообщения", widget=forms.Textarea(attrs={"rows": 4}))
