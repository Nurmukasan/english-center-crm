from django.db import models
from django.contrib.auth.models import User


class Student(models.Model):
    """Ученик"""
    name = models.CharField(max_length=100, verbose_name="Имя")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон")
    parent_name = models.CharField(max_length=100, blank=True, verbose_name="Родитель")
    parent_phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон родителя")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Ученик"
        verbose_name_plural = "Ученики"


class Group(models.Model):
    """Группа"""
    name = models.CharField(max_length=100, verbose_name="Название группы")
    teacher = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='teaching_groups', verbose_name="Учитель")
    schedule = models.CharField(max_length=200, blank=True, verbose_name="Расписание")
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Цена за месяц")
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Группа"
        verbose_name_plural = "Группы"


class Enrollment(models.Model):
    """Запись ученика в группу"""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='enrollments')
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='enrollments')
    enrolled_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.student.name} → {self.group.name}"

    class Meta:
        verbose_name = "Запись в группу"
        verbose_name_plural = "Записи в группы"


class Lesson(models.Model):
    """Урок"""
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='lessons')
    date = models.DateField(verbose_name="Дата")
    topic = models.CharField(max_length=200, blank=True, verbose_name="Тема урока")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.group.name} — {self.date}"

    class Meta:
        verbose_name = "Урок"
        verbose_name_plural = "Уроки"
        ordering = ['-date']


class Attendance(models.Model):
    """Посещаемость"""
    STATUS_CHOICES = [
        ('present', 'Присутствовал'),
        ('absent', 'Отсутствовал'),
        ('late', 'Опоздал'),
    ]
    
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name='attendances')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='attendances')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='absent', verbose_name="Статус")

    def __str__(self):
        return f"{self.student.name} — {self.lesson.date} — {self.status}"

    class Meta:
        verbose_name = "Посещаемость"
        verbose_name_plural = "Посещаемость"
        unique_together = ['lesson', 'student']


class Payment(models.Model):
    """Оплата за 4-недельный цикл"""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='payments')
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='payments')
    cycle_number = models.IntegerField(default=1, verbose_name="Цикл")
    
    start_date = models.DateField(null=True, blank=True, verbose_name="Начало периода")
    end_date = models.DateField(null=True, blank=True, verbose_name="Конец периода")
    
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Сумма")
    paid_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Оплачено")
    is_paid = models.BooleanField(default=False, verbose_name="Полностью оплачено")
    is_partial = models.BooleanField(default=False, verbose_name="Частичная оплата")
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата оплаты")
    marked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='marked_payments')
    
    def remaining_amount(self):
        return self.amount - self.paid_amount

    def __str__(self):
        status = '✅' if self.is_paid else ('🟡' if self.is_partial else '❌')
        return f"{self.student.name} — {self.group.name} — Цикл {self.cycle_number} — {status}"

    class Meta:
        verbose_name = "Оплата"
        verbose_name_plural = "Оплаты"
        unique_together = ['student', 'group', 'cycle_number']