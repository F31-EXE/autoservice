from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditEvent(models.Model):
    """Who did what and when (spec 11). ``data`` must not contain personal data."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    station = models.ForeignKey(
        "accounts.ServiceStation", on_delete=models.CASCADE, null=True, blank=True, related_name="+"
    )
    action = models.CharField(max_length=64)
    object_type = models.CharField(max_length=64)
    object_id = models.CharField(max_length=64)
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "событие аудита"
        verbose_name_plural = "журнал аудита"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["object_type", "object_id"])]

    def __str__(self) -> str:
        return f"{self.action} {self.object_type}#{self.object_id}"


def audit(actor, action: str, obj, station=None, **data) -> AuditEvent:
    return AuditEvent.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        station=station,
        action=action,
        object_type=obj._meta.label_lower,
        object_id=str(obj.pk),
        data=data,
    )
