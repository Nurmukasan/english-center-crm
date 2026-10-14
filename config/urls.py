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
    path('students/', views.students_list, name='students_list'),
    path('students/add/', views.add_student, name='add_student'),
    path('group/<int:group_id>/history/', views.lesson_history, name='lesson_history'),
    path('group/<int:group_id>/history/add/', views.add_past_lesson, name='add_past_lesson'),
    path('lesson/<int:lesson_id>/save/', views.save_lesson, name='save_lesson'),
    path('lesson/<int:lesson_id>/cancel/', views.toggle_lesson_cancel, name='toggle_lesson_cancel'),
    path('schedule/', views.weekly_schedule, name='weekly_schedule'),
    path('export/', views.export_excel, name='export_excel'),
    path('payments-management/', views.payment_management, name='payment_management'),
    path('accountant-stats/', views.accountant_stats, name='accountant_stats'),
    path('accountant-stats/export/', views.export_income_excel, name='export_income_excel'),
    path('groups/add/', views.add_group, name='add_group'),
    path('student/<int:student_id>/remove-from-group/<int:group_id>/', views.remove_student_from_group, name='remove_student_from_group'),
    path('student/<int:student_id>/archive/', views.archive_student, name='archive_student'),
    path('student/<int:student_id>/restore/', views.restore_student, name='restore_student'),
    path('group/<int:group_id>/archive/', views.archive_group, name='archive_group'),
    path('group/<int:group_id>/restore/', views.restore_group, name='restore_group'),
    path('student/<int:student_id>/add-to-group/', views.add_existing_student_to_group, name='add_existing_student_to_group'),
    path('enrollment/<int:enrollment_id>/toggle-book/', views.toggle_book_status, name='toggle_book_status'),
    path('books-status/', views.books_status, name='books_status'),
    path('enrollment/toggle-book/', views.toggle_book_status, name='toggle_book_status_bulk'),
    path('student/<int:student_id>/edit/', views.edit_student, name='edit_student'),
    path('group/<int:group_id>/edit/', views.edit_group, name='edit_group'),
    path('payments/', views.payments_page, name='payments_page'),
    path('payments/group/<int:group_id>/', views.group_payment_detail, name='group_payment_detail'),
    path('payments/group/<int:group_id>/toggle/', views.toggle_student_payment, name='toggle_student_payment'),
    path('book/<int:book_id>/read/', views.book_reader, name='book_reader'),
    path('books/<int:book_id>/', views.book_detail, name='book_detail'),
    path('group/<int:group_id>/homework/', views.mark_homework, name='mark_homework'),
    path('calibration/create/', views.create_calibration, name='create_calibration'),
    path('students/<int:student_id>/', views.student_profile, name='student_profile'),
    path('group/<int:group_id>/stars/', views.give_stars, name='give_stars'),
    path('star-rating/', views.star_rating, name='star_rating'),
    path('api/search/', views.search_api, name='search_api'),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)