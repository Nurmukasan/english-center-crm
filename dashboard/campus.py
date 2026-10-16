"""Обложки карточек групп на главной: рисунок достопримечательности по названию группы.

Группы в центре названы в честь университетов (Melbourne, Glasgow, NUS, PSL…) — по слову
в названии подбирается рисунок (dashboard/templates/dashboard/campus/*.svg) и свой оттенок.
Все рисунки в одном стиле: одинаковые светлота и насыщенность, разный только оттенок, —
поэтому карточки рядом смотрятся одной серией. Рисунки свои (логотипы университетов —
чужие товарные знаки, их не используем).

Не нашлось подходящего рисунка — обложкой становится фото группы (если загружено),
а без фото — университетский корпус в цвете группы.
"""
import re

ART = 'dashboard/campus/{}.svg'

# рисунок, оттенок, насыщенность (1 — обычная), подпись, слова в названии группы
THEMES = [
    ('melbourne', 222, 1, 'Австралия', ('melbourne', 'мельбурн', 'monash', 'монаш')),
    ('sydney', 192, 1, 'Австралия', ('sydney', 'сидней', 'unsw', 'anu', 'australia', 'австралия')),
    ('glasgow', 330, 0.9, 'Шотландия', ('glasgow', 'глазго', 'edinburgh', 'эдинбург', 'strathclyde', 'aberdeen',
                                       'scotland', 'шотландия')),
    ('singapore', 26, 1.35, 'Сингапур', ('nus', 'ntu', 'singapore', 'сингапур')),
    ('paris', 262, 0.9, 'Франция', ('psl', 'paris', 'париж', 'sorbonne', 'сорбонна', 'france', 'франция')),
    ('canada', 2, 1, 'Канада', ('canada', 'канада', 'toronto', 'торонто', 'mcgill', 'макгилл', 'ubc', 'waterloo',
                               'ottawa', 'оттава', 'montreal', 'монреаль', 'vancouver', 'ванкувер')),
    ('london', 168, 0.9, 'Англия', ('london', 'лондон', 'ucl', 'imperial', 'lse', 'kcl', 'westminster')),
    ('oxford', 212, 1, 'Англия', ('oxford', 'оксфорд')),
    ('cambridge', 155, 0.9, 'Англия', ('cambridge', 'кембридж')),
    ('boston', 355, 1, 'США', ('harvard', 'гарвард', 'mit', 'boston', 'бостон', 'yale', 'йель', 'princeton', 'принстон')),
    ('california', 40, 1.2, 'США', ('stanford', 'стэнфорд', 'berkeley', 'беркли', 'ucla', 'caltech', 'usc',
                                   'california', 'калифорния')),
    ('newyork', 230, 0.8, 'США', ('columbia', 'nyu', 'cornell', 'new york', 'нью-йорк')),
    ('tokyo', 345, 0.9, 'Япония', ('tokyo', 'токио', 'todai', 'waseda', 'keio', 'kyoto', 'киото', 'japan', 'япония')),
    ('seoul', 300, 0.85, 'Южная Корея', ('seoul', 'сеул', 'snu', 'kaist', 'yonsei', 'korea', 'корея')),
]

WORD = re.compile(r'[a-zа-яё0-9]+')


def words(text):
    return WORD.findall((text or '').lower())


def theme_for(name):
    """Рисунок для группы по её названию; не подошёл ни один — None"""
    tokens = words(name)
    joined = ' ' + ' '.join(tokens) + ' '
    for art, hue, sat, label, keys in THEMES:
        for key in keys:
            parts = words(key)
            # «nus» — только отдельным словом (не внутри «bonus»), «new york» — словами подряд
            if parts and ' ' + ' '.join(parts) + ' ' in joined:
                # Числа — строкой: шаблон по-русски написал бы «0,9», а CSS нужна точка
                return {'key': art, 'art': ART.format(art), 'hue': str(hue), 'sat': f'{sat:g}', 'label': label}
    return None


def cover_for(group, fallback_hue):
    """Как оформить карточку группы: рисунок, оттенок, подпись. art=None — показать фото группы"""
    theme = theme_for(group.name)
    if theme:
        return theme
    has_photo = bool(getattr(group, 'photo', None))
    return {'key': 'photo' if has_photo else 'campus', 'art': None if has_photo else ART.format('campus'),
            'hue': str(fallback_hue), 'sat': '0.8', 'label': ''}
