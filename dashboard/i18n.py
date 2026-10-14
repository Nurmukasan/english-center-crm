"""Английская версия интерфейса — без Google Translate и без мигания.

Шаблоны и сообщения остаются на русском. Если выбран английский (cookie ee_lang=en),
EnglishMiddleware переводит готовую страницу по словарю dashboard/locale_en.py ещё
на сервере, и браузер сразу получает страницу на английском.

Как ищется перевод фразы:
  1. фраза целиком — PHRASES['Сохранить'];
  2. фраза, где числа, месяцы и дни недели заменены метками {n}, {m}, {d}:
     'Пришли 3 из 5' → PHRASES['Пришли {n} из {n}'] = 'Present {n} of {n}';
  3. регулярные выражения PATTERNS — для сообщений с именами учеников и групп.
Не нашлось перевода — остаётся русский текст. Имена и всё внутри class="notranslate"
не трогаются, как и поля ввода. Что осталось без перевода: python manage.py i18n_missing
"""
import html
import json
import re

from .locale_en import PATTERNS, PHRASES

LANG_COOKIE = 'ee_lang'
CYR = re.compile('[А-Яа-яЁё]')
SPACES = re.compile(r'\s+')

MONTHS_EN = ['January', 'February', 'March', 'April', 'May', 'June',
             'July', 'August', 'September', 'October', 'November', 'December']
WEEKDAYS_EN = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

# Все формы месяцев и дней недели, которые встречаются в интерфейсе → (номер, короткая ли форма)
MONTH_FORMS = {}
for i, forms in enumerate([
    ('январь', 'января', 'янв'), ('февраль', 'февраля', 'фев'), ('март', 'марта', 'мар'),
    ('апрель', 'апреля', 'апр'), ('май', 'мая', 'май'), ('июнь', 'июня', 'июн'),
    ('июль', 'июля', 'июл'), ('август', 'августа', 'авг'), ('сентябрь', 'сентября', 'сен'),
    ('октябрь', 'октября', 'окт'), ('ноябрь', 'ноября', 'ноя'), ('декабрь', 'декабря', 'дек'),
]):
    for j, form in enumerate(forms):
        MONTH_FORMS.setdefault(form, (i, j == 2 and form != 'май'))
WEEKDAY_FORMS = {}
for i, forms in enumerate([
    ('понедельник', 'пн'), ('вторник', 'вт'), ('среда', 'среду', 'ср'), ('четверг', 'чт'),
    ('пятница', 'пятницу', 'пт'), ('суббота', 'субботу', 'сб'), ('воскресенье', 'вс'),
]):
    for form in forms:
        WEEKDAY_FORMS[form] = (i, len(form) == 2)

NUMBER = r'\d+(?:[.,:   ]\d+)*'
TOKEN_RE = re.compile(
    r'(?P<n>' + NUMBER + r')'
    r'|\b(?P<m>' + '|'.join(sorted(MONTH_FORMS, key=len, reverse=True)) + r')\b'
    r'|\b(?P<d>' + '|'.join(sorted(WEEKDAY_FORMS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE,
)
PLACEHOLDER = re.compile(r'\{([nmd])(\d*)\}')
PLURAL = re.compile(r'\{s:([^|}]*)\|([^}]*)\}')
COMPILED_PATTERNS = [(re.compile(p), t) for p, t in PATTERNS]

# Сюда складываются фразы без перевода (для manage.py i18n_missing)
missing = None


def _token_text(kind, value):
    """Число остаётся числом, месяц и день недели — по-английски"""
    if kind == 'n':
        return value
    if kind == 'm':
        index, short = MONTH_FORMS[value.lower()]
        return MONTHS_EN[index][:3] if short else MONTHS_EN[index]
    index, short = WEEKDAY_FORMS[value.lower()]
    return WEEKDAYS_EN[index][:3] if short else WEEKDAYS_EN[index]


def _fill(template, tokens):
    """Подставить значения меток: {n} — по порядку, {n1} — по номеру"""
    counters = {'n': 0, 'm': 0, 'd': 0}
    by_kind = {k: [v for kind, v in tokens if kind == k] for k in counters}

    def put(match):
        kind, index = match.group(1), match.group(2)
        if index:
            i = int(index)
        else:
            i = counters[kind]
            counters[kind] += 1
        values = by_kind[kind]
        return _token_text(kind, values[i]) if i < len(values) else match.group(0)
    # {s:lesson|lessons} — английское число по ближайшему числу слева
    if '{s:' in template:
        def plural(match):
            before = re.findall(r'\{n\d*\}|' + NUMBER, template[:match.start()])
            numbers = [v for kind, v in tokens if kind == 'n']
            last = numbers[min(len(before), len(numbers)) - 1] if numbers and before else ''
            return match.group(1) if last.strip() == '1' else match.group(2)
        template = PLURAL.sub(plural, template)
    return PLACEHOLDER.sub(put, template)


def phrase_key(text):
    """Ключ словаря: числа, месяцы и дни недели → {n}, {m}, {d}"""
    return TOKEN_RE.sub(lambda m: '{%s}' % m.lastgroup, text)


def translate_phrase(text):
    """Перевод одной фразы без пробелов по краям; None — перевода нет"""
    if text in PHRASES:
        return PHRASES[text]
    tokens = []

    def mark(match):
        kind = match.lastgroup
        tokens.append((kind, match.group(0)))
        return '{%s}' % kind
    key = TOKEN_RE.sub(mark, text)
    if tokens:
        if key in PHRASES:
            return _fill(PHRASES[key], tokens)
        # Остались только числа, месяцы и дни недели: «Октябрь 2026», «Пн»
        if not CYR.search(key):
            return _fill(key, tokens)
    for pattern, template in COMPILED_PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            groups = [translate_text(g) if g and CYR.search(g) else (g or '') for g in match.groups()]
            return template.format(*groups)
    return None


def translate_text(text, report=True):
    """Перевод куска текста с сохранением пробелов по краям; без перевода — как было"""
    if not CYR.search(text):
        return text
    core = text.strip()
    if '\n' in core or '  ' in core:
        core = SPACES.sub(' ', core)
    result = translate_phrase(core)
    if result is None:
        if missing is not None and report:
            missing.add(phrase_key(core))
        return text
    start = len(text) - len(text.lstrip())
    end = len(text.rstrip())
    return text[:start] + result + text[end:]


# ---------------- HTML ----------------

ATTRS = ('placeholder', 'title', 'aria-label', 'data-confirm', 'data-label', 'alt', 'content', 'data-name')
ATTR_RE = re.compile(r'(\s(?:%s)=")([^"]*)(")' % '|'.join(ATTRS))
VALUE_RE = re.compile(r'(\svalue=")([^"]*)(")')
HANDLER_RE = re.compile(r'(\son[a-z]+=")([^"]*)(")')
TAG_NAME = re.compile(r'<\s*(/?)\s*([a-zA-Z][\w-]*)')
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}
PARTS = re.compile(r'<!--.*?-->|<(script|style|textarea)\b[^>]*>.*?</\1\s*>|<[^>]*>|[^<]+', re.S | re.I)
JS_STRING = re.compile(r"'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|`(?:\\.|[^`\\])*`")


def _attr_value(value, report=True):
    translated = translate_text(html.unescape(value), report)
    return value if translated == html.unescape(value) else html.escape(translated, quote=True)


def _handler_value(value):
    """onclick="confirm('…')" — строки внутри обработчиков событий"""
    code = html.unescape(value)
    translated = _translate_js(code)
    return value if translated == code else html.escape(translated, quote=True)


def _translate_tag(tag):
    if not CYR.search(tag):
        return tag
    # data-name — это и подписи (месяцы на графике), и имена учеников для поиска: имена не отчитываем
    tag = ATTR_RE.sub(lambda m: m.group(1) + _attr_value(m.group(2), 'data-name' not in m.group(1)) + m.group(3), tag)
    tag = HANDLER_RE.sub(lambda m: m.group(1) + _handler_value(m.group(2)) + m.group(3), tag)
    if re.search(r'type="(?:submit|button)"', tag):
        tag = VALUE_RE.sub(lambda m: m.group(1) + _attr_value(m.group(2)) + m.group(3), tag)
    return tag


def _translate_js(code):
    """Строки внутри <script>: только те, что целиком есть в словаре"""
    def one(match):
        literal = match.group(0)
        quote, body = literal[0], literal[1:-1]
        if not CYR.search(body):
            return literal
        if '<' in body and '>' in body:
            translated = translate_html(body)
        else:
            translated = translate_text(body)
        if translated == body:
            return literal
        translated = re.sub(r'(?<!\\)' + re.escape(quote), lambda m: '\\' + quote, translated)
        return quote + translated + quote
    return JS_STRING.sub(one, code)


def translate_html(content):
    out = []
    stack = []   # открытые теги: (имя, внутри notranslate)
    for match in PARTS.finditer(content):
        part = match.group(0)
        skip = bool(stack and stack[-1][1])
        if part.startswith('<!--'):
            out.append(part)
        elif match.group(1):
            name = match.group(1).lower()
            if name == 'script' and not skip:
                open_end = part.index('>') + 1
                close_start = part.lower().rindex('</script')
                code = part[open_end:close_start]
                if 'application/json' in part[:open_end]:
                    code = _translate_json_script(code)
                else:
                    code = _translate_js(code)
                part = part[:open_end] + code + part[close_start:]
            out.append(part)   # style и textarea — как есть
        elif part.startswith('<'):
            tag = TAG_NAME.match(part)
            if not tag:
                out.append(part)
                continue
            closing, name = tag.group(1) == '/', tag.group(2).lower()
            if closing:
                for i in range(len(stack) - 1, -1, -1):
                    if stack[i][0] == name:
                        del stack[i:]
                        break
                out.append(part)
                continue
            no = skip or bool(re.search(r'class="[^"]*\bnotranslate\b', part))
            out.append(part if no else _translate_tag(part))
            if name not in VOID and not part.endswith('/>'):
                stack.append((name, no))
        else:
            out.append(part if skip else _translate_text_node(part))
    result = ''.join(out)
    return re.sub(r'<html lang="ru"', '<html lang="en"', result, count=1)


JSON_SCRIPT_ESCAPES = {ord('>'): '\\u003E', ord('<'): '\\u003C', ord('&'): '\\u0026'}


def _translate_json_script(code):
    """{{ data|json_script }}: кириллица там в виде \\uXXXX, поэтому переводим данные, а не текст"""
    try:
        data = json.loads(code)
    except ValueError:
        return code
    translated = translate_data(data)
    if translated == data:
        return code
    return json.dumps(translated).translate(JSON_SCRIPT_ESCAPES)


def _translate_text_node(text):
    if not CYR.search(text):
        return text
    translated = translate_text(html.unescape(text))
    if translated == html.unescape(text):
        return text
    return html.escape(translated, quote=False)


# ---------------- JSON ----------------

# Данные, которые уходят клиентам в WhatsApp, остаются на русском
NO_TRANSLATE_KEYS = {'vars', 'text', 'name', 'sub', 'phone_display'}


def translate_data(value):
    if isinstance(value, str):
        return translate_text(value)
    if isinstance(value, list):
        return [translate_data(v) for v in value]
    if isinstance(value, dict):
        return {k: v if k in NO_TRANSLATE_KEYS else translate_data(v) for k, v in value.items()}
    return value


def current_language(request):
    """'ru', 'en' или 'kk' (казахский пока переводит Google Translate, если выбран на лендинге)"""
    google = request.COOKIES.get('googtrans', '')
    if 'kk' in google:
        return 'kk'
    chosen = request.COOKIES.get(LANG_COOKIE)
    if chosen is None:
        # Английский, выбранный раньше через Google Translate, подхватываем сами
        return 'en' if google.endswith('/en') else 'ru'
    return 'en' if chosen == 'en' else 'ru'


def is_english(request):
    return current_language(request) == 'en'


def language(request):
    """Контекст-процессор: {{ ee_lang }} в любом шаблоне"""
    return {'ee_lang': current_language(request)}


class EnglishMiddleware:
    """Переводит готовые страницы и ответы AJAX, если выбран английский"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not is_english(request) or request.path.startswith('/admin/') or getattr(response, 'streaming', False):
            return response
        content_type = response.get('Content-Type', '')
        if 'text/html' in content_type:
            charset = response.charset or 'utf-8'
            response.content = translate_html(response.content.decode(charset)).encode(charset)
        elif 'application/json' in content_type:
            try:
                data = json.loads(response.content)
            except ValueError:
                return response
            response.content = json.dumps(translate_data(data), ensure_ascii=False).encode('utf-8')
        else:
            return response
        if response.has_header('Content-Length'):
            response['Content-Length'] = str(len(response.content))
        return response
