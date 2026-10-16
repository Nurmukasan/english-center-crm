import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


# Порядок важен: «Upper-Intermediate» и «Pre-Intermediate» проверяем раньше «Intermediate»
LEVEL_WORDS = [
    (6, ('upper', 'b2')),
    (4, ('pre-int', 'pre int', 'preint', 'pre-inter', 'a2+')),
    (8, ('proficiency', 'c2')),
    (7, ('advanced', 'c1')),
    (5, ('intermediate', 'b1')),
    (3, ('elementary', 'a2')),
    (2, ('beginner', 'a1')),
    (1, ('starter', 'kids', 'kid ')),
]


def guess_levels(apps, schema_editor):
    """Уровень групп по названию: «Beginner A1» → A1, «Pre-Intermediate» → Pre-Intermediate"""
    Group = apps.get_model("dashboard", "Group")
    for group in Group.objects.filter(group_type="group"):
        name = group.name.lower() + " "
        for level, words in LEVEL_WORDS:
            if any(w in name for w in words):
                group.level = level
                group.save(update_fields=["level"])
                break


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0026_default_price_3000"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="group",
            name="level",
            field=models.PositiveSmallIntegerField(
                blank=True,
                choices=[
                    (1, "Starter — дети"),
                    (2, "Beginner A1"),
                    (3, "Elementary A2"),
                    (4, "Pre-Intermediate"),
                    (5, "Intermediate B1"),
                    (6, "Upper-Intermediate B2"),
                    (7, "Advanced C1"),
                    (8, "Proficiency C2"),
                ],
                null=True,
                verbose_name="Уровень",
            ),
        ),
        migrations.AddField(
            model_name="payment",
            name="method",
            field=models.CharField(
                choices=[("bank", "Банк"), ("cash", "Наличные")],
                default="bank",
                max_length=10,
                verbose_name="Способ оплаты",
            ),
        ),
        migrations.CreateModel(
            name="InboxMessage",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("complaint", "Жалоба"),
                            ("comment", "Комментарий"),
                            ("calibration", "Калибровка"),
                        ],
                        max_length=20,
                        verbose_name="Категория",
                    ),
                ),
                ("text", models.TextField(blank=True, verbose_name="Текст")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "read_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="Прочитано"
                    ),
                ),
                (
                    "resolution",
                    models.CharField(
                        blank=True, max_length=200, verbose_name="Решение"
                    ),
                ),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                (
                    "author",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sent_inbox_messages",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="От кого",
                    ),
                ),
                (
                    "calibration",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="message",
                        to="dashboard.levelcalibration",
                    ),
                ),
                (
                    "group",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="inbox_messages",
                        to="dashboard.group",
                        verbose_name="Группа",
                    ),
                ),
                (
                    "read_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "resolved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="inbox_messages",
                        to="dashboard.student",
                        verbose_name="Ученик",
                    ),
                ),
            ],
            options={
                "verbose_name": "Сообщение",
                "verbose_name_plural": "Сообщения",
                "ordering": ["-created_at"],
            },
        ),
        migrations.RunPython(guess_levels, migrations.RunPython.noop),
    ]
