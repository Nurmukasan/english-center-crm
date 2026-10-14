from django.db import migrations, models


def keep_green_api(apps, schema_editor):
    """Кто уже подключил Green API — остаётся на нём; остальным по умолчанию WAHA"""
    WhatsAppSettings = apps.get_model("dashboard", "WhatsAppSettings")
    WhatsAppSettings.objects.exclude(instance_id="").update(provider="green")
    WhatsAppSettings.objects.filter(instance_id="").update(api_url="http://localhost:3000")


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0024_whatsapp_broadcasts"),
    ]

    operations = [
        migrations.AddField(
            model_name="whatsappsettings",
            name="provider",
            field=models.CharField(
                choices=[
                    ("waha", "WAHA — свой сервер, бесплатно"),
                    ("green", "Green API — платный сервис"),
                ],
                default="waha",
                max_length=10,
                verbose_name="Способ подключения",
            ),
        ),
        migrations.AlterField(
            model_name="broadcast",
            name="mode",
            field=models.CharField(
                choices=[("api", "Автоматически"), ("manual", "Вручную по ссылкам")],
                default="manual",
                max_length=10,
                verbose_name="Как отправляем",
            ),
        ),
        migrations.AlterField(
            model_name="whatsappsettings",
            name="api_url",
            field=models.CharField(
                default="http://localhost:3000", max_length=200, verbose_name="Адрес"
            ),
        ),
        migrations.RunPython(keep_green_api, migrations.RunPython.noop),
    ]
