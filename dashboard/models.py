from django.db import models
from django.contrib.auth.models import User


class Student(models.Model):
    """Ученик"""
    name = models.CharField(max_length=100, verbose_name="Имя")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон")
    parent_name = models.CharField(max_length=100, blank=True, verbose_name="Родитель")
    parent_phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон родителя")
    school = models.CharField(max_length=200, blank=True, verbose_name="Школа")
    grade = models.CharField(max_length=20, blank=True, verbose_name="Класс")
    age = models.IntegerField(null=True, blank=True, verbose_name="Возраст")
    price_per_lesson = models.DecimalField(
        max_digits=10, decimal_places=2, default=3000,
        verbose_name="Цена за 1 урок"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, verbose_name="Учится (не в архиве)")
    archived_at = models.DateTimeField(null=True, blank=True, verbose_name="В архиве с")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Ученик"
        verbose_name_plural = "Ученики"


class Book(models.Model):
    """Книга для группы"""
    title = models.CharField(max_length=200, verbose_name="Название книги")
    photo = models.ImageField(upload_to='books/', blank=True, null=True, verbose_name="Фото обложки")
    pdf_file = models.FileField(upload_to='books_pdf/', blank=True, null=True, verbose_name="PDF книги")
    created_at = models.DateTimeField(auto_now_add=True)
    quantity = models.IntegerField(default=0, verbose_name="Количество книг")

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = "Книга"
        verbose_name_plural = "Книги"


class Group(models.Model):
    """Группа"""
    GROUP_TYPE_CHOICES = [
        ('group', 'Групповая'),
        ('individual', 'Индивидуальная'),
    ]
    
    name = models.CharField(max_length=100, verbose_name="Название группы")
    group_type = models.CharField(max_length=20, choices=GROUP_TYPE_CHOICES, default='group', verbose_name="Тип")
    teacher = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='teaching_groups', verbose_name="Учитель")
    teachers = models.ManyToManyField(User, related_name='group_teachers', blank=True, verbose_name="Дополнительные учителя")
    schedule = models.CharField(max_length=200, blank=True, verbose_name="Расписание")
    cycle_start_day = models.IntegerField(
        default=11,
        verbose_name="День начала цикла",
        help_text="Число месяца, с которого начинается учебный цикл (2-28). Если 6 — цикл идёт с 6 числа этого месяца по 5 число следующего."
    )
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    created_at = models.DateTimeField(auto_now_add=True)
    books = models.ManyToManyField(Book, related_name='groups', blank=True, verbose_name="Книги")
    photo = models.ImageField(upload_to='group_photos/', blank=True, null=True, verbose_name="Фото группы")
    whatsapp_chat_id = models.CharField(max_length=60, blank=True, verbose_name="Чат WhatsApp",
                                        help_text="ID группы в WhatsApp (…@g.us) — для рассылок в групповой чат")

    def __str__(self):
        return self.name

    def get_schedule_display(self):
        days_names = {0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'}
        parts = []
        for slot in self.schedule_slots.all().order_by('day_of_week'):
            parts.append(f"{days_names[slot.day_of_week]} {slot.start_time.strftime('%H:%M')}-{slot.end_time.strftime('%H:%M')}")
        return ', '.join(parts) if parts else self.schedule

    class Meta:
        verbose_name = "Группа"
        verbose_name_plural = "Группы"


class ScheduleSlot(models.Model):
    """Время занятия для конкретного дня недели"""
    DAY_CHOICES = [
        (0, 'Пн'),
        (1, 'Вт'),
        (2, 'Ср'),
        (3, 'Чт'),
        (4, 'Пт'),
        (5, 'Сб'),
        (6, 'Вс'),
    ]
    
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='schedule_slots', verbose_name="Группа")
    day_of_week = models.IntegerField(choices=DAY_CHOICES, verbose_name="День недели")
    start_time = models.TimeField(verbose_name="Начало")
    end_time = models.TimeField(verbose_name="Конец")
    
    def __str__(self):
        return f"{self.get_day_of_week_display()} {self.start_time}-{self.end_time}"
    
    class Meta:
        verbose_name = "Расписание"
        verbose_name_plural = "Расписания"
        ordering = ['day_of_week', 'start_time']


class Enrollment(models.Model):
    """Запись ученика в группу"""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='enrollments')
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='enrollments')
    enrolled_at = models.DateTimeField(auto_now_add=True)
    has_book = models.BooleanField(default=False, verbose_name="Есть книга")
    book_needed = models.BooleanField(default=False, verbose_name="Требуется книга")
    book = models.ForeignKey(Book, on_delete=models.SET_NULL, null=True, blank=True, related_name='enrollments', verbose_name="Запрошенная книга")
    book_comment = models.TextField(blank=True, verbose_name="Комментарий к запросу книги")

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
    is_cancelled = models.BooleanField(default=False, verbose_name="Урок отменён")
    edited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='edited_lessons', verbose_name="Кто правил задним числом")
    edited_at = models.DateTimeField(null=True, blank=True, verbose_name="Когда правили")

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
    stars = models.PositiveSmallIntegerField(default=0, verbose_name="Звёзды за урок")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name='attendances')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='attendances')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='absent', verbose_name="Статус")
    homework_done = models.BooleanField(default=False, verbose_name="Домашка сделана")

    def __str__(self):
        return f"{self.student.name} — {self.lesson.date} — {self.status}"

    class Meta:
        verbose_name = "Посещаемость"
        verbose_name_plural = "Посещаемость"
        unique_together = ['lesson', 'student']


class Payment(models.Model):
    """Оплата за цикл"""
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

class LevelCalibration(models.Model):
    """Калибровка уровня ученика"""
    DIRECTION_CHOICES = [
        ('up', 'Уровень выше'),
        ('down', 'Уровень ниже'),
    ]
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='calibrations')
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='calibrations')
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, verbose_name="Направление")
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='calibrations')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.student.name} — {self.get_direction_display()}"

    class Meta:
        verbose_name = "Калибровка уровня"
        verbose_name_plural = "Калибровки уровня"
        ordering = ['-created_at']


class WhatsAppSettings(models.Model):
    """Подключение к WhatsApp (одна запись на весь центр).

    WAHA — бесплатная программа на своём сервере: api_url — её адрес, token — API-ключ.
    Green API — платный сервис: api_url, instance_id (idInstance) и token (apiTokenInstance).
    """
    PROVIDER_CHOICES = [
        ('waha', 'WAHA — свой сервер, бесплатно'),
        ('green', 'Green API — платный сервис'),
    ]
    DEFAULT_URLS = {'waha': 'http://localhost:3000', 'green': 'https://api.green-api.com'}
    provider = models.CharField(max_length=10, choices=PROVIDER_CHOICES, default='waha', verbose_name="Способ подключения")
    api_url = models.CharField(max_length=200, default='http://localhost:3000', verbose_name="Адрес")
    instance_id = models.CharField(max_length=50, blank=True, verbose_name="idInstance")
    token = models.CharField(max_length=200, blank=True, verbose_name="apiTokenInstance")
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def is_configured(self):
        if self.provider == 'waha':
            return bool(self.api_url and self.token)
        return bool(self.instance_id and self.token)

    def __str__(self):
        if not self.is_configured:
            return 'WhatsApp — не подключено'
        return f"WAHA {self.api_url}" if self.provider == 'waha' else f"Green API {self.instance_id}"

    class Meta:
        verbose_name = "Подключение WhatsApp"
        verbose_name_plural = "Подключение WhatsApp"


class Broadcast(models.Model):
    """Рассылка в WhatsApp: что, кому и когда отправили"""
    KIND_CHOICES = [
        ('payment', 'Напоминание об оплате'),
        ('news', 'Новость'),
    ]
    AUDIENCE_CHOICES = [
        ('group', 'Группы WhatsApp'),
        ('parent', 'Родители'),
        ('student', 'Ученики'),
        ('teacher', 'Учителя'),
    ]
    MODE_CHOICES = [
        ('api', 'Автоматически'),
        ('manual', 'Вручную по ссылкам'),
    ]
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, verbose_name="Тип")
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, verbose_name="Кому")
    mode = models.CharField(max_length=10, choices=MODE_CHOICES, default='manual', verbose_name="Как отправляем")
    title = models.CharField(max_length=200, verbose_name="Название")
    text = models.TextField(verbose_name="Шаблон текста")
    year = models.IntegerField(null=True, blank=True)
    month = models.IntegerField(null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='broadcasts')
    created_at = models.DateTimeField(auto_now_add=True)

    MONTHS = ['январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
              'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь']

    @property
    def month_label(self):
        return self.MONTHS[self.month - 1] if self.month else ''

    def __str__(self):
        return f"{self.title} — {self.created_at:%d.%m.%Y}"

    class Meta:
        verbose_name = "Рассылка"
        verbose_name_plural = "Рассылки"
        ordering = ['-created_at']


class BroadcastMessage(models.Model):
    """Одно сообщение рассылки — одному человеку или в один групповой чат"""
    STATUS_CHOICES = [
        ('pending', 'Ждёт отправки'),
        ('sent', 'Отправлено'),
        ('failed', 'Ошибка'),
        ('skipped', 'Пропущено'),
    ]
    broadcast = models.ForeignKey(Broadcast, on_delete=models.CASCADE, related_name='messages')
    recipient = models.CharField(max_length=200, verbose_name="Получатель")
    note = models.CharField(max_length=200, blank=True, verbose_name="Пояснение")
    phone = models.CharField(max_length=20, blank=True, verbose_name="Телефон")
    chat_id = models.CharField(max_length=60, blank=True, verbose_name="Чат WhatsApp")
    text = models.TextField(verbose_name="Текст")
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Сумма")
    student = models.ForeignKey(Student, on_delete=models.SET_NULL, null=True, blank=True, related_name='broadcast_messages')
    group = models.ForeignKey(Group, on_delete=models.SET_NULL, null=True, blank=True, related_name='broadcast_messages')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', verbose_name="Статус")
    error = models.CharField(max_length=300, blank=True, verbose_name="Ошибка")
    sent_at = models.DateTimeField(null=True, blank=True, verbose_name="Отправлено")

    def __str__(self):
        return f"{self.recipient} — {self.get_status_display()}"

    class Meta:
        verbose_name = "Сообщение рассылки"
        verbose_name_plural = "Сообщения рассылки"
        ordering = ['id']
