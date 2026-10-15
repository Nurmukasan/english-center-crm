from django.db import migrations, models


def fill_empty_prices(apps, schema_editor):
    """Ученикам без цены (0) — 3000 ₸ за урок; у кого цена уже задана, не трогаем"""
    Student = apps.get_model("dashboard", "Student")
    Student.objects.filter(price_per_lesson=0).update(price_per_lesson=3000)


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0025_whatsapp_waha"),
    ]

    operations = [
        migrations.AlterField(
            model_name="student",
            name="price_per_lesson",
            field=models.DecimalField(
                decimal_places=2,
                default=3000,
                max_digits=10,
                verbose_name="Цена за 1 урок",
            ),
        ),
        migrations.RunPython(fill_empty_prices, migrations.RunPython.noop),
    ]
