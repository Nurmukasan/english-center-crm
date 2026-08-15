from django.db import models
from django.contrib.auth.models import User


class Profile(models.Model):
    """Расширенный профиль пользователя"""
    ROLE_CHOICES = [
        ('admin', 'Администратор'),
        ('teacher', 'Учитель'),
        ('developer', 'Разработчик'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='teacher', verbose_name="Роль")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон")

    def __str__(self):
        return f"{self.user.username} — {self.get_role_display()}"

    class Meta:
        verbose_name = "Профиль"
        verbose_name_plural = "Профили"