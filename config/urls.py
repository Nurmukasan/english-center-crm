from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from dashboard import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.login_view, name='login'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile, name='profile'),
    path('group/<int:group_id>/', views.group_detail, name='group_detail'),
    path('group/<int:group_id>/attendance/', views.mark_attendance, name='mark_attendance'),
    path('group/<int:group_id>/payment/', views.toggle_payment, name='toggle_payment'),
    path('students/', views.students_list, name='students_list'),
    path('students/add/', views.add_student, name='add_student'),
    path('payments/', views.payments_list, name='payments_list'),
    path('group/<int:group_id>/history/', views.lesson_history, name='lesson_history'),
    path('schedule/', views.weekly_schedule, name='weekly_schedule'),
    path('export/', views.export_excel, name='export_excel'),
    path('payments-management/', views.payment_management, name='payment_management'),
    path('payments-management/toggle/<int:payment_id>/', views.toggle_payment_management, name='toggle_payment_management'),
    path('payments-management/partial/<int:payment_id>/', views.partial_payment, name='partial_payment'),
    path('accountant-stats/', views.accountant_stats, name='accountant_stats'),
    path('accountant-stats/export/', views.export_income_excel, name='export_income_excel'),
    path('groups/add/', views.add_group, name='add_group'),
    path('student/<int:student_id>/remove-from-group/<int:group_id>/', views.remove_student_from_group, name='remove_student_from_group'),
    path('student/<int:student_id>/delete/', views.delete_student, name='delete_student'),
    path('group/<int:group_id>/delete/', views.delete_group, name='delete_group'),
    path('student/<int:student_id>/add-to-group/', views.add_existing_student_to_group, name='add_existing_student_to_group'),
    path('enrollment/<int:enrollment_id>/toggle-book/', views.toggle_book_status, name='toggle_book_status'),
    path('books-status/', views.books_status, name='books_status'),
    path('enrollment/toggle-book/', views.toggle_book_status, name='toggle_book_status_bulk'),
    path('student/<int:student_id>/edit/', views.edit_student, name='edit_student'),
    path('group/<int:group_id>/edit/', views.edit_group, name='edit_group'),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)