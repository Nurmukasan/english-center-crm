from django.db import migrations, models


def copy_books_forward(apps, schema_editor):
    Group = apps.get_model('dashboard', 'Group')
    for group in Group.objects.all():
        if group.book_id:
            group.books.add(group.book_id)


def copy_books_backward(apps, schema_editor):
    Group = apps.get_model('dashboard', 'Group')
    for group in Group.objects.all():
        first_book = group.books.first()
        if first_book:
            group.book = first_book
            group.save()


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0014_book_pdf_file_delete_bookpage'),  # ← ВСТАВЬ своё имя последней миграции
    ]

    operations = [
        migrations.AddField(
            model_name='group',
            name='books',
            field=models.ManyToManyField(
                blank=True, related_name='groups', to='dashboard.book', verbose_name='Книги'
            ),
        ),
        migrations.RunPython(copy_books_forward, copy_books_backward),
        migrations.RemoveField(
            model_name='group',
            name='book',
        ),
    ]