from django.contrib import admin
from django.urls import path
from dashboard import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.login_view, name='login'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('logout/', views.logout_view, name='logout'),
    path('group/<int:group_id>/', views.group_detail, name='group_detail'),
    path('group/<int:group_id>/attendance/', views.mark_attendance, name='mark_attendance'),
    path('group/<int:group_id>/payment/', views.toggle_payment, name='toggle_payment'),
    path('students/', views.students_list, name='students_list'),
    path('students/add/', views.add_student, name='add_student'),
    path('payments/', views.payments_list, name='payments_list'),
    path('group/<int:group_id>/history/', views.lesson_history, name='lesson_history'),
    path('schedule/', views.weekly_schedule, name='weekly_schedule'),
]