from django.db import models


class IntegrationSettings(models.Model):
    """Stores third-party integration credentials. Single-row table."""

    jira_key = models.CharField(max_length=255, blank=True)
    jira_email = models.EmailField(blank=True)
    jira_url = models.URLField(blank=True)
    google_token = models.CharField(max_length=255, blank=True)
    openai_token = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Integration Settings"
        verbose_name_plural = "Integration Settings"

    def __str__(self) -> str:  # pragma: no cover - trivial display helper
        return "Integration Settings"

    @classmethod
    def load(cls):
        """Returns the singleton instance, creating it if missing."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
