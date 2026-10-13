from django.db import migrations, models


def copy_stars(apps, schema_editor):
    Attendance = apps.get_model('dashboard', 'Attendance')
    Attendance.objects.filter(star_earned=True).update(stars=1)


def copy_stars_back(apps, schema_editor):
    Attendance = apps.get_model('dashboard', 'Attendance')
    Attendance.objects.filter(stars__gt=0).update(star_earned=True)


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0021_enrollment_book_comment'),
    ]

    operations = [
        migrations.AddField(
            model_name='attendance',
            name='stars',
            field=models.PositiveSmallIntegerField(default=0, verbose_name='Звёзды за урок'),
        ),
        migrations.RunPython(copy_stars, copy_stars_back),
        migrations.RemoveField(
            model_name='attendance',
            name='star_earned',
        ),
    ]
