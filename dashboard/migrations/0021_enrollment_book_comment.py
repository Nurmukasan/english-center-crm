from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0020_attendance_star_earned'),
    ]

    operations = [
        migrations.AddField(
            model_name='enrollment',
            name='book_comment',
            field=models.TextField(blank=True, verbose_name='Комментарий к запросу книги'),
        ),
    ]
