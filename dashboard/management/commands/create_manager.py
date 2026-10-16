"""Создать менеджера (или сменить ему пароль, если такой логин уже есть).

Менеджер ведёт группы и учеников и работает с оплатами — см. MANAGE_ROLES и MONEY_ROLES в views.py.
Пароль не хранится в коде: его вводят при запуске.

    python manage.py create_manager aksunkar          # пароль спросит
    python manage.py create_manager aksunkar 'пароль'
"""
from getpass import getpass

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from dashboard.security import password_problems
from users.models import Profile


class Command(BaseCommand):
    help = 'Создать менеджера или сменить ему пароль'

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('password', nargs='?')

    def handle(self, *args, username, password=None, **options):
        username = username.strip()
        if not username:
            raise CommandError('Укажите логин')
        if not password:
            password = getpass('Пароль: ')
            if password != getpass('Ещё раз: '):
                raise CommandError('Пароли не совпали')
        problems = password_problems(password, User(username=username))
        if 'Пароль — не короче 8 символов' in problems:
            raise CommandError('Пароль слишком короткий — нужно хотя бы 8 символов')

        user, created = User.objects.get_or_create(username=username)
        if not created and (user.is_superuser or getattr(user, 'profile', None) and user.profile.role != 'manager'):
            raise CommandError(f'Логин {username} уже занят пользователем с другой ролью — выберите другой')
        user.set_password(password)
        user.is_active = True
        user.save()
        Profile.objects.update_or_create(user=user, defaults={'role': 'manager'})
        self.stdout.write(self.style.SUCCESS(
            f'Менеджер {username} создан' if created else f'Менеджеру {username} обновлён пароль'))
        for problem in problems:
            # Не запрещаем — пароль выбирает владелец, но предупреждаем
            self.stdout.write(self.style.WARNING(f'Внимание: {problem.lower()}. Лучше придумать пароль сложнее.'))
