"""Какие фразы остались без английского перевода.

Обходит страницы CRM под каждой ролью (как пользователь с выбранным английским)
и печатает русские фразы, которых нет в dashboard/locale_en.py. Ничего не меняет.

    python manage.py i18n_missing
"""
import logging
from collections import Counter

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.test import Client
from django.urls import reverse

from dashboard import i18n
from dashboard.models import Book, Group, Student
from dashboard.views import get_current_period


class Command(BaseCommand):
    help = 'Показать фразы интерфейса без английского перевода'

    def handle(self, *args, **options):
        logging.getLogger('django.request').setLevel(logging.ERROR)
        settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']
        found = Counter()
        where = {}
        year, month = get_current_period()

        pages = ['dashboard', 'students_list', 'add_student', 'weekly_schedule', 'individual_lessons',
                 'add_individual', 'payments_page', 'accountant_stats', 'add_group', 'books_status',
                 'star_rating', 'profile']
        urls = [reverse(name) for name in pages]
        urls += ['/dashboard/?archived=1', '/students/?archived=1',
                 f'/payments/?year={year}&month={month}', f'/payments/?year={year + 1}&month=1']
        groups = list(Group.objects.filter(is_active=True).order_by('group_type', 'id')[:3]) + \
            list(Group.objects.filter(group_type='individual')[:1]) + list(Group.objects.filter(is_active=False)[:1])
        for g in groups:
            urls += [reverse('group_detail', args=[g.id]), reverse('lesson_history', args=[g.id]),
                     reverse('edit_group', args=[g.id])]
        for s in Student.objects.order_by('-is_active', 'id')[:3]:
            urls += [reverse('student_profile', args=[s.id]), reverse('edit_student', args=[s.id]),
                     reverse('add_existing_student_to_group', args=[s.id])]
        for b in Book.objects.all()[:2]:
            urls += [reverse('book_detail', args=[b.id]), reverse('book_reader', args=[b.id])]

        users = []
        for role in ['admin', 'developer', 'accountant', 'teacher']:
            user = User.objects.filter(profile__role=role).first()
            if user:
                users.append(user)
        nobody = User.objects.filter(profile__isnull=True, is_superuser=False).first()

        def visit(client, url, who):
            i18n.missing = set()
            client.get(url, follow=True)
            for phrase in i18n.missing:
                found[phrase] += 1
                where.setdefault(phrase, f'{url} ({who})')

        anonymous = Client()
        anonymous.cookies['ee_lang'] = 'en'
        visit(anonymous, '/', 'гость')
        for user in users + ([nobody] if nobody else []):
            client = Client()
            client.cookies['ee_lang'] = 'en'
            client.force_login(user)
            for url in urls:
                visit(client, url, user.username)
        i18n.missing = None

        # Одна буква — инициалы на аватарах и сочетание Ctrl+Л, переводить нечего
        found = Counter({phrase: n for phrase, n in found.items() if len(phrase) > 1})
        if not found:
            self.stdout.write(self.style.SUCCESS('Всё переведено'))
            return
        for phrase, count in sorted(found.items(), key=lambda x: x[0]):
            self.stdout.write(f'{phrase!r}  ← {where[phrase]}')
        self.stdout.write(self.style.WARNING(
            f'Без перевода: {len(found)} фраз. Имена учеников и групп переводить не нужно — '
            'остальное добавьте в dashboard/locale_en.py'))
