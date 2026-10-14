import json
from datetime import time, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from users.models import Profile
from .models import (
    Attendance, Book, Enrollment, Group, LevelCalibration, Lesson, Payment, ScheduleSlot, Student,
)
from .views import get_current_period


def make_user(username, role=None, superuser=False):
    """Пользователь с ролью (role=None — без профиля)"""
    if superuser:
        user = User.objects.create_superuser(username, f'{username}@example.com', 'pass')
    else:
        user = User.objects.create_user(username, password='pass')
    if role:
        Profile.objects.create(user=user, role=role)
    return user


# Быстрый хешер паролей — тесты в разы быстрее
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CRMTestCase(TestCase):
    def setUp(self):
        self.admin = make_user('admin', 'admin')
        self.teacher = make_user('teacher', 'teacher')
        self.other_teacher = make_user('other', 'teacher')
        self.accountant = make_user('acc', 'accountant')

        self.group = Group.objects.create(name='Beginner', teacher=self.teacher)
        self.other_group = Group.objects.create(name='Альфа', teacher=self.other_teacher)
        self.student = Student.objects.create(name='Алия', price_per_lesson=Decimal('4000'))
        self.other_student = Student.objects.create(name='Тимур')
        self.enrollment = Enrollment.objects.create(student=self.student, group=self.group)
        self.other_enrollment = Enrollment.objects.create(student=self.other_student, group=self.other_group)

    def login(self, user):
        self.client.force_login(user)


class RoleTests(CRMTestCase):
    def test_user_without_profile_has_no_access(self):
        self.login(make_user('nobody'))
        for url in ['/dashboard/', '/students/add/', '/export/', f'/group/{self.group.id}/']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, url)
        response = self.client.post(f'/group/{self.group.id}/attendance/', {'student_id': self.student.id, 'status': 'present'})
        self.assertEqual(response.status_code, 403)

    def test_superuser_without_profile_is_admin(self):
        boss = make_user('boss', superuser=True)
        self.login(boss)
        self.assertEqual(self.client.get('/dashboard/').status_code, 200)
        self.assertEqual(self.client.get('/export/').status_code, 200)
        # Смена телефона создаёт профиль, но не понижает до учителя
        self.client.post('/profile/', {'action': 'change_phone', 'phone': '+7 700'})
        self.assertEqual(Profile.objects.get(user=boss).role, 'admin')

    def test_teacher_cannot_mark_other_group(self):
        self.login(self.teacher)
        response = self.client.post(f'/group/{self.other_group.id}/homework/', {'student_id': self.other_student.id, 'done': '1'})
        self.assertFalse(response.json()['success'])
        self.assertFalse(Attendance.objects.exists())

    def test_teacher_cannot_mark_student_from_another_group(self):
        self.login(self.teacher)
        response = self.client.post(f'/group/{self.group.id}/homework/', {'student_id': self.other_student.id, 'done': '1'})
        self.assertFalse(response.json()['success'])

    def test_attendance_status_is_validated(self):
        ScheduleSlot.objects.create(group=self.group, day_of_week=timezone.localdate().weekday(), start_time=time(10), end_time=time(11))
        self.login(self.teacher)
        response = self.client.post(f'/group/{self.group.id}/attendance/', {'student_id': self.student.id, 'status': 'hacked'})
        self.assertFalse(response.json()['success'])
        response = self.client.post(f'/group/{self.group.id}/attendance/', {'student_id': self.student.id, 'status': 'late'})
        self.assertTrue(response.json()['success'])
        self.assertEqual(Attendance.objects.get(student=self.student).status, 'late')

    def test_teacher_cannot_touch_books_and_calibration_of_other_group(self):
        self.login(self.teacher)
        self.client.post('/enrollment/toggle-book/', {'enrollment_ids': str(self.other_enrollment.id), 'action': 'need_book', 'comment': 'x'})
        self.other_enrollment.refresh_from_db()
        self.assertFalse(self.other_enrollment.book_needed)
        response = self.client.post('/calibration/create/', {'student_id': self.other_student.id, 'group_id': self.other_group.id, 'direction': 'up'})
        self.assertFalse(response.json()['success'])
        self.assertFalse(LevelCalibration.objects.exists())


class ArchiveTests(CRMTestCase):
    def setUp(self):
        super().setUp()
        Payment.objects.create(student=self.student, group=self.group, cycle_number=202610, amount=1000, is_paid=True)
        self.login(self.admin)

    def test_delete_links_do_not_work_by_get(self):
        for url in [f'/student/{self.student.id}/archive/', f'/group/{self.group.id}/archive/',
                    f'/student/{self.student.id}/remove-from-group/{self.group.id}/']:
            self.assertEqual(self.client.get(url).status_code, 405, url)
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_active)

    def test_archive_student_keeps_history(self):
        self.client.post(f'/student/{self.student.id}/archive/')
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_active)
        self.assertFalse(Enrollment.objects.filter(student=self.student).exists())
        self.assertTrue(Payment.objects.filter(student=self.student).exists())
        names = lambda url: [d['student'].name for d in self.client.get(url).context['student_data']]
        self.assertNotIn('Алия', names('/students/'))
        self.assertIn('Алия', names('/students/?archived=1'))

    def test_restore_student(self):
        self.client.post(f'/student/{self.student.id}/archive/')
        response = self.client.post(f'/student/{self.student.id}/restore/')
        self.assertRedirects(response, f'/student/{self.student.id}/add-to-group/')
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_active)

    def test_adding_archived_student_to_group_restores_him(self):
        self.client.post(f'/student/{self.student.id}/archive/')
        self.client.post(f'/student/{self.student.id}/add-to-group/', {'groups': [self.group.id]})
        self.student.refresh_from_db()
        self.assertTrue(self.student.is_active)

    def test_archive_group_keeps_lessons_and_payments(self):
        Lesson.objects.create(group=self.group, date=timezone.localdate())
        self.client.post(f'/group/{self.group.id}/archive/')
        self.group.refresh_from_db()
        self.assertFalse(self.group.is_active)
        self.assertTrue(Lesson.objects.filter(group=self.group).exists())
        self.assertTrue(Payment.objects.filter(group=self.group).exists())
        names = lambda url: [g.name for g in self.client.get(url).context['group_cards']]
        self.assertNotIn('Beginner', names('/dashboard/'))
        self.assertIn('Beginner', names('/dashboard/?archived=1'))
        self.client.post(f'/group/{self.group.id}/restore/')
        self.group.refresh_from_db()
        self.assertTrue(self.group.is_active)

    def test_teacher_cannot_archive(self):
        self.login(self.teacher)
        self.client.post(f'/group/{self.group.id}/archive/')
        self.group.refresh_from_db()
        self.assertTrue(self.group.is_active)


class SearchTests(CRMTestCase):
    def test_special_characters_do_not_crash(self):
        self.login(self.admin)
        for url in ['/dashboard/?search=(', '/students/?search=[', '/payments/?year=2026&month=10&search=(*']:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_cyrillic_search_ignores_case(self):
        self.login(self.admin)
        self.assertContains(self.client.get('/dashboard/?search=АЛЬ'), 'Альфа')
        self.assertContains(self.client.get('/students/?search=алИ'), 'Алия')


class PaymentSummaryTests(CRMTestCase):
    def test_dashboard_counts_unpaid_students_without_payment_rows(self):
        third = Student.objects.create(name='Дана')
        Enrollment.objects.create(student=third, group=self.group)
        y, m = get_current_period(self.group.cycle_start_day)
        Payment.objects.create(student=self.student, group=self.group, cycle_number=y * 100 + m, amount=1, is_paid=True)
        self.login(self.admin)
        response = self.client.get('/dashboard/')
        # Оплатила одна из трёх (Алия; Дана и Тимур — нет)
        self.assertEqual(response.context['paid_count'], 1)
        self.assertEqual(response.context['unpaid_count'], 2)


class LessonTests(CRMTestCase):
    def setUp(self):
        super().setUp()
        Enrollment.objects.filter(id=self.enrollment.id).update(enrolled_at=timezone.now() - timedelta(days=60))
        self.lesson = Lesson.objects.create(group=self.group, date=timezone.localdate() - timedelta(days=3))
        Attendance.objects.create(lesson=self.lesson, student=self.student, status='late')

    def save(self, user, rows, topic=''):
        self.login(user)
        return self.client.post(f'/lesson/{self.lesson.id}/save/', json.dumps({'topic': topic, 'rows': rows}),
                                content_type='application/json')

    def test_history_counts_late_as_present(self):
        self.login(self.teacher)
        response = self.client.get(f'/group/{self.group.id}/history/')
        self.assertContains(response, 'Алия (опоздал)')
        self.assertEqual(response.context['lesson_data'][0]['present_count'], 1)

    def test_edit_past_lesson(self):
        response = self.save(self.teacher, [
            {'student_id': self.student.id, 'status': 'present', 'homework': True, 'stars': 500},
            {'student_id': self.other_student.id, 'status': 'present', 'homework': True, 'stars': 1},
            {'student_id': self.student.id, 'status': 'bogus'},
        ], topic='Past Simple')
        self.assertTrue(response.json()['success'])
        att = Attendance.objects.get(lesson=self.lesson, student=self.student)
        self.assertEqual((att.status, att.homework_done, att.stars), ('present', True, 100))
        # Чужой ученик не попал в урок
        self.assertFalse(Attendance.objects.filter(student=self.other_student).exists())
        self.lesson.refresh_from_db()
        self.assertEqual((self.lesson.topic, self.lesson.edited_by), ('Past Simple', self.teacher))

    def test_other_teacher_cannot_edit(self):
        response = self.save(self.other_teacher, [{'student_id': self.student.id, 'status': 'present'}])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Attendance.objects.get(student=self.student).status, 'late')

    def test_cancel_and_restore_lesson(self):
        self.login(self.teacher)
        self.client.post(f'/lesson/{self.lesson.id}/cancel/')
        self.lesson.refresh_from_db()
        self.assertTrue(self.lesson.is_cancelled)
        self.assertFalse(self.lesson.attendances.exists())
        self.assertFalse(self.save(self.teacher, [{'student_id': self.student.id, 'status': 'present'}]).json()['success'])

        self.client.post(f'/lesson/{self.lesson.id}/cancel/')
        self.lesson.refresh_from_db()
        self.assertFalse(self.lesson.is_cancelled)
        self.assertEqual(self.lesson.attendances.get().status, 'absent')

    def test_cancelled_today_lesson_blocks_marks(self):
        today = Lesson.objects.create(group=self.group, date=timezone.localdate(), is_cancelled=True)
        ScheduleSlot.objects.create(group=self.group, day_of_week=today.date.weekday(), start_time=time(10), end_time=time(11))
        self.login(self.teacher)
        for url, data in [('attendance', {'student_id': self.student.id, 'status': 'present'}),
                          ('homework', {'student_id': self.student.id, 'done': '1'}),
                          ('stars', {'stars': f'{self.student.id}:2'})]:
            self.assertFalse(self.client.post(f'/group/{self.group.id}/{url}/', data).json()['success'], url)

    def test_add_past_lesson(self):
        self.login(self.teacher)
        future = timezone.localdate() + timedelta(days=1)
        self.client.post(f'/group/{self.group.id}/history/add/', {'date': future.isoformat()})
        self.assertFalse(Lesson.objects.filter(date=future).exists())

        day = timezone.localdate() - timedelta(days=10)
        response = self.client.post(f'/group/{self.group.id}/history/add/', {'date': day.isoformat()})
        lesson = Lesson.objects.get(group=self.group, date=day)
        self.assertIn(f'?open={lesson.id}', response['Location'])
        self.assertEqual(lesson.attendances.get().student, self.student)

    def test_lesson_day_comes_from_schedule_slots(self):
        ScheduleSlot.objects.create(group=self.group, day_of_week=timezone.localdate().weekday(), start_time=time(10), end_time=time(11))
        self.login(self.teacher)
        self.client.get(f'/group/{self.group.id}/')
        self.assertTrue(Lesson.objects.filter(group=self.group, date=timezone.localdate()).exists())

    def test_archived_group_does_not_create_lessons(self):
        ScheduleSlot.objects.create(group=self.group, day_of_week=timezone.localdate().weekday(), start_time=time(10), end_time=time(11))
        self.group.is_active = False
        self.group.save()
        self.login(self.admin)
        self.client.get(f'/group/{self.group.id}/')
        self.assertFalse(Lesson.objects.filter(group=self.group, date=timezone.localdate()).exists())


class CleanupTests(CRMTestCase):
    def test_old_payment_pages_are_gone(self):
        self.login(self.admin)
        self.assertEqual(self.client.get('/payments-list/').status_code, 404)
        self.assertRedirects(self.client.get('/payments-management/'), '/payments/')

    def test_logout_drops_pending_messages(self):
        self.login(self.teacher)
        self.client.get(f'/group/{self.other_group.id}/')  # «нет доступа» — сообщение
        self.client.get('/logout/')
        self.assertFalse(list(self.client.get('/').context['messages']))
