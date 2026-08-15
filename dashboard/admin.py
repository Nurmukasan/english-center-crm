from django.contrib import admin
from .models import Student, Group, Enrollment, Lesson, Attendance, Payment


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ['name', 'phone', 'parent_name', 'parent_phone']
    search_fields = ['name', 'phone']


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ['name', 'teacher', 'schedule', 'price', 'is_active']
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
    list_display = ['student', 'lesson', 'status']
    list_filter = ['status', 'lesson__group']


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['student', 'group', 'month', 'year', 'amount', 'is_paid']
    list_filter = ['is_paid', 'month', 'year', 'group']