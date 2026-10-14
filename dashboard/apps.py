from django.apps import AppConfig


class DashboardConfig(AppConfig):
    # Как в миграциях (их создавали на Django 6): иначе Django 5.2 на сервере видит «изменения» id
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dashboard'
