from django.contrib import admin
from .models import Student, Group, Enrollment, Lesson, Attendance, Payment, Book
from .models import Student, Group, Enrollment, Lesson, Attendance, Payment, Book, LevelCalibration, LoginEvent, GameScore, Application

@admin.register(LevelCalibration)
class LevelCalibrationAdmin(admin.ModelAdmin):
    list_display = ['student', 'group', 'direction', 'created_by', 'created_at']
    list_filter = ['direction', 'group']
    search_fields = ['student__name', 'comment']

@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ['title', 'quantity']


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'parent_name', 'parent_phone', 'price_per_lesson']
    search_fields = ['name', 'phone']


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    filter_horizontal = ('books', 'teachers')
    list_display = ['name', 'teacher', 'schedule', 'cycle_start_day', 'is_active']
    list_filter = ['is_active', 'teacher']


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ['student', 'group', 'enrolled_at']


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ['group', 'date', 'start_time', 'teacher', 'topic']
    list_filter = ['group', 'date']


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ['student', 'lesson', 'status', 'homework_done', 'stars']
    list_filter = ['status', 'homework_done', 'lesson__group']


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['student', 'group', 'cycle_number', 'start_date', 'end_date', 'amount', 'is_paid', 'marked_by']
    list_filter = ['is_paid', 'group', 'cycle_number']
    search_fields = ['student__name']


from .models import Broadcast, BroadcastMessage, WhatsAppSettings


@admin.register(WhatsAppSettings)
class WhatsAppSettingsAdmin(admin.ModelAdmin):
    list_display = ['instance_id', 'api_url', 'updated_at']


class BroadcastMessageInline(admin.TabularInline):
    model = BroadcastMessage
    extra = 0
    fields = ['recipient', 'phone', 'chat_id', 'amount', 'status', 'error', 'sent_at']
    readonly_fields = fields


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    list_display = ['title', 'kind', 'audience', 'mode', 'created_by', 'created_at']
    list_filter = ['kind', 'audience', 'mode']
    inlines = [BroadcastMessageInline]


from .models import InboxMessage


@admin.register(InboxMessage)
class InboxMessageAdmin(admin.ModelAdmin):
    list_display = ['category', 'student', 'group', 'author', 'created_at', 'read_at', 'resolution']
    list_filter = ['category']
    search_fields = ['student__name', 'text']


@admin.register(LoginEvent)
class LoginEventAdmin(admin.ModelAdmin):
    """Журнал входов — только смотреть: записи делает сама CRM"""
    list_display = ['created_at', 'username', 'success', 'note', 'ip', 'user_agent']
    list_filter = ['success', 'note']
    search_fields = ['username', 'ip']
    readonly_fields = [f.name for f in LoginEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(GameScore)
class GameScoreAdmin(admin.ModelAdmin):
    """Результаты тетриса: отсюда можно удалить нечестный рекорд"""
    list_display = ('user', 'score', 'lines', 'level', 'duration', 'created_at')
    list_filter = ('game',)
    search_fields = ('user__username', 'user__first_name')
    date_hierarchy = 'created_at'


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'kind', 'first_name', 'last_name', 'age', 'phone', 'parent_phone', 'status']
    list_filter = ['kind', 'status', 'level']
    search_fields = ['first_name', 'last_name', 'phone', 'parent_phone']
    date_hierarchy = 'created_at'
    raw_id_fields = ['student']
