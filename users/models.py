from django.db import models
from django.contrib.auth.models import User


class Profile(models.Model):
    """Расширенный профиль пользователя"""
    ROLE_CHOICES = [
        ('admin', 'Администратор'),
        ('teacher', 'Учитель'),
        ('accountant', 'Бухгалтер'),
        ('developer', 'Разработчик'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='teacher', verbose_name="Роль")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон")
    photo = models.ImageField(upload_to='profile_photos/', blank=True, null=True, verbose_name="Фото")

    def __str__(self):
        return f"{self.user.username} — {self.get_role_display()}"

    class Meta:
        verbose_name = "Профиль"
        verbose_name_plural = "Профили"