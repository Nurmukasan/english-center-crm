"""Тетрис для сотрудников: кнопка в меню CRM открывает игру в новой вкладке.

Справа в игре — «Топ-5 центра»: лучший результат каждого сотрудника. После партии браузер
присылает очки сюда (POST /game/tetris/score/), сервер проверяет, что они правдоподобны, и
возвращает обновлённую таблицу. Нечестный рекорд можно удалить в /admin/ → «Тетрис: результаты».
Музыка и звуки синтезируются прямо в браузере — файлов нет (templates/dashboard/tetris.html).
"""
import json

from django.core.cache import cache
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .models import GameScore
from .views import crm_view, to_int

GAME = 'tetris'
TOP = 5
SCAN = 3000          # сколько лучших партий просматривать, собирая топ (по одной на сотрудника)
MIN_GAP = 5          # секунд между двумя сохранёнными партиями одного сотрудника

# Подсказки и надписи игры — сюда, а не в JS: английская версия переведёт их сама
TEXTS = {
    'single': 'Линия!', 'double': 'Две линии!', 'triple': 'Три линии!', 'tetris': 'ТЕТРИС!',
    'combo': 'Комбо ×{n}', 'b2b': 'Тетрис подряд!', 'level': 'Уровень {n}', 'record': 'Новый личный рекорд!',
    'top': 'Вы в топ-5 центра!', 'place': 'Место в центре: {n}', 'saving': 'Сохраняем результат…',
    'saved': 'Результат сохранён', 'not_saved': 'Не удалось сохранить результат — проверьте интернет',
    'empty': 'Пока никто не играл — станьте первым!', 'you': 'вы', 'paused': 'Пауза',
}


def display_name(user):
    return user.get_full_name() or user.username


def leaderboard(viewer=None, limit=TOP):
    """Лучший результат каждого сотрудника, по убыванию"""
    rows, seen = [], set()
    for s in GameScore.objects.filter(game=GAME).select_related('user', 'user__profile').order_by('-score', 'created_at')[:SCAN]:
        if s.user_id in seen:
            continue
        seen.add(s.user_id)
        profile = getattr(s.user, 'profile', None)
        rows.append({
            'place': len(rows) + 1,
            'name': display_name(s.user),
            'initial': (display_name(s.user)[:1] or '?').upper(),
            'photo': profile.photo.url if profile and profile.photo else '',
            'score': s.score, 'lines': s.lines, 'level': s.level,
            'date': s.created_at.strftime('%d.%m.%Y'),
            'me': bool(viewer and s.user_id == viewer.id),
        })
        if len(rows) >= limit:
            break
    return rows


def my_stats(user):
    """Личный рекорд и место среди сотрудников"""
    best = GameScore.objects.filter(game=GAME, user=user).aggregate(best=Max('score'))['best']
    if best is None:
        return {'best': 0, 'place': None, 'games': 0}
    better = (GameScore.objects.filter(game=GAME).values('user').annotate(best=Max('score'))
              .filter(best__gt=best).count())
    return {'best': best, 'place': better + 1, 'games': GameScore.objects.filter(game=GAME, user=user).count()}


def plausible(score, lines, level, duration):
    """Очки, которые могла дать честная партия: игра идёт в браузере, совсем фантастику не принимаем"""
    if not (1 <= level <= 99 and 0 <= lines <= 5000 and 1 <= duration <= 6 * 3600 and 0 < score <= 10_000_000):
        return False
    if level > lines // 10 + 1:                      # уровень растёт каждые 10 линий
        return False
    if lines > duration * 3:                         # больше трёх линий в секунду не собрать
        return False
    pieces = lines * 2.5 + duration * 3 + 50
    return score <= lines * 1500 * level + pieces * 44 + 1000   # с запасом: длинные комбо дают больше тетрисов


@crm_view
def tetris_page(request):
    return render(request, 'dashboard/tetris.html', {
        'top': leaderboard(request.user), 'me': my_stats(request.user), 'texts': TEXTS,
    })


@crm_view
@require_POST
def tetris_score(request):
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    score, lines, level, duration = (to_int(data.get(k), -1) for k in ('score', 'lines', 'level', 'duration'))
    if not plausible(score, lines, level, duration):
        return JsonResponse({'ok': False, 'error': 'Такой результат не похож на настоящий'}, status=400)
    key = f'game-score:{request.user.pk}'
    if not cache.add(key, 1, MIN_GAP):
        return JsonResponse({'ok': False, 'error': 'Слишком часто — подождите несколько секунд'}, status=429)
    before = my_stats(request.user)['best']
    GameScore.objects.create(user=request.user, game=GAME, score=score, lines=lines, level=level, duration=duration)
    me = my_stats(request.user)
    top = leaderboard(request.user)
    return JsonResponse({'ok': True, 'record': score > before, 'top': top, 'me': me,
                         'in_top': any(row['me'] for row in top)})
