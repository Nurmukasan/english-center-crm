from django.contrib import admin
from .models import Student, Group, Enrollment, Lesson, Attendance, Payment, Book
from .models import Student, Group, Enrollment, Lesson, Attendance, Payment, Book, LevelCalibration

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
    list_display = ['group', 'date', 'topic']
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
