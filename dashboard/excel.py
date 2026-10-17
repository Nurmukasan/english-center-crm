"""Выгрузки в Excel: «Статистика доходов» и «Общий отчёт».

Файлы живые. Итоги, проценты и сводка — формулы Excel (СУММЕСЛИМН, СЧЁТЕСЛИМН, ПРОМЕЖУТОЧНЫЕ.ИТОГИ),
которые считают из листов с данными: поменяли статус или отфильтровали таблицу — цифры пересчитались.
Рядом с каждой формулой записан и её готовый результат: его видно даже в предпросмотре файла
на телефоне, где формулы не считаются. Таблицы — настоящие «умные таблицы» Excel с фильтром
и сортировкой в заголовках; статусы раскрашены, на сводке — графики.
"""
import io
from datetime import date, datetime, timedelta
from decimal import Decimal

import xlsxwriter
from django.db.models import Q
from django.utils import timezone

FONT = 'Arial'
INK, INK_2, MUTED, LINE, SOFT = '#1F1C1A', '#44403C', '#78716C', '#E8E5E1', '#F7F6F4'
# Цвета рядов — проверенная палитра (различимы и при нарушениях цветового зрения); статусы — отдельно
BLUE, ORANGE, AQUA = '#2A78D6', '#EB6834', '#1BAF7A'
GOOD, WARN, SERIOUS, BAD = '#0CA30C', '#FAB219', '#EC835A', '#D03B3B'
GOOD_BG, WARN_BG, BAD_BG = '#E3F6E3', '#FFF3D6', '#FBE3E3'
# Светлые оттенки для полосок в ячейках — число поверх них читается
BLUE_TINT, ORANGE_TINT, BAD_TINT, WARN_TINT = '#BCD5F4', '#F8C7AE', '#F2B8B8', '#FCDE96'

# Денежный формат Excel с валютой «₸ казахский (Казахстан)» (код языка 43F): без него Excel на русской
# Windows считает ячейку не денежной и при правке формата подставляет рубли
MONEY = '#,##0 [$₸-43F];-#,##0 [$₸-43F];"—"'
PCT = '0%'
CHANGE = '+0.0%;-0.0%;0%'
DATE = 'dd.mm.yyyy'
MONTH = '[$-419]mmmm yyyy'
MONTH_SHORT = '[$-419]mmm yy'

PAID, PARTIAL, DEBT, WAITING, NO_AMOUNT = 'Оплачено', 'Частично', 'Долг', 'Ждём оплату', 'Без суммы'
PRESENT, LATE, ABSENT = 'Был', 'Опоздал', 'Не был'


class F:
    """Формула и её готовый результат"""
    __slots__ = ('formula', 'value')

    def __init__(self, formula, value):
        self.formula, self.value = formula, value


def excel_date(d):
    """Дата → число Excel (для готового результата формулы, которая возвращает дату)"""
    return (d - date(1899, 12, 30)).days


def ratio(part, total):
    return part / total if total else 0


def quote(sheet):
    return "'" + sheet.replace("'", "''") + "'"


def col_range(sheet, col, first, last):
    """'Лист'!$C$2:$C$40"""
    return f"{quote(sheet)}!${col}${first}:${col}${last}"


def unique_labels(objects, name):
    """Подписи без повторов: одинаковые имена различаем номером — иначе формулы по имени сложили бы их вместе"""
    counts = {}
    for obj in objects:
        counts[name(obj)] = counts.get(name(obj), 0) + 1
    return {obj.id: name(obj) if counts[name(obj)] == 1 else f'{name(obj)} (№{obj.id})' for obj in objects}


class Book:
    """Обёртка над XlsxWriter: форматы, таблицы, плитки сводки, графики в одном стиле"""

    def __init__(self, title, subject):
        self.buffer = io.BytesIO()
        self.wb = xlsxwriter.Workbook(self.buffer, {'in_memory': True})
        self.wb.set_properties({'title': title, 'subject': subject, 'author': 'Enjoy English CRM', 'company': 'Enjoy English'})
        self._formats = {}
        self._tables = 0

    def fmt(self, **props):
        base = {'font_name': FONT, 'font_size': 10, 'font_color': INK, 'valign': 'vcenter'}
        base.update(props)
        key = tuple(sorted((k, str(v)) for k, v in base.items()))
        if key not in self._formats:
            self._formats[key] = self.wb.add_format(base)
        return self._formats[key]

    KINDS = {
        'text': {}, 'name': {'bold': True}, 'wrap': {'text_wrap': True}, 'center': {'align': 'center'},
        'money': {'num_format': MONEY}, 'int': {'num_format': '#,##0'}, 'pct': {'num_format': PCT},
        'change': {'num_format': CHANGE}, 'date': {'num_format': DATE, 'align': 'left'},
        'month': {'num_format': MONTH, 'align': 'left'}, 'muted': {'font_color': MUTED},
    }

    def kind(self, kind, **extra):
        return self.fmt(**{**self.KINDS[kind], **extra})

    def put(self, ws, row, col, value, fmt):
        if isinstance(value, F):
            ws.write_formula(row, col, value.formula, fmt, value.value)
        elif value is None or value == '':
            ws.write_blank(row, col, None, fmt)
        elif isinstance(value, bool):
            ws.write_string(row, col, 'Да' if value else 'Нет', fmt)
        elif isinstance(value, (datetime, date)):
            ws.write_datetime(row, col, value, fmt)
        elif isinstance(value, (int, float, Decimal)):
            ws.write_number(row, col, float(value), fmt)
        else:
            ws.write_string(row, col, str(value), fmt)

    def sheet(self, name, tab_color=None):
        ws = self.wb.add_worksheet(name)
        ws.set_landscape()
        ws.set_paper(9)          # A4
        ws.fit_to_pages(1, 0)    # по ширине на страницу
        ws.set_margins(0.4, 0.4, 0.6, 0.6)
        ws.set_footer(f'&L&"Arial"&8Enjoy English · {name}&R&"Arial"&8стр. &P из &N')
        if tab_color:
            ws.set_tab_color(tab_color)
        return ws

    def title(self, ws, text, note=None, last_col=8):
        ws.set_row(0, 30)
        ws.write_string(0, 0, text, self.fmt(bold=True, font_size=16))
        if note:
            ws.write_string(1, 0, note, self.fmt(font_color=MUTED, font_size=9))

    def table(self, ws, top, columns, rows, totals=None, total_label='Итого'):
        """Умная таблица с фильтром/сортировкой. columns — [(заголовок, ширина, вид)];
        rows — списки значений или F; totals — {номер колонки: 'sum' | функция(first, last) → F}.
        Возвращает (первая строка данных, последняя, строка итогов) — номера как в Excel (с 1)."""
        self._tables += 1
        header = self.fmt(bold=True, font_color='#FFFFFF', bg_color='#2B2724', text_wrap=True, align='left', border=1, border_color='#2B2724')
        data = rows or [[''] * len(columns)]
        first, last = top + 2, top + 1 + len(data)
        ws.add_table(top, 0, top + len(data), len(columns) - 1, {
            'name': f'Table_{self._tables}', 'style': 'Table Style Light 1', 'autofilter': True, 'banded_rows': True,
            'columns': [{'header': h, 'header_format': header} for h, _, _ in columns],
        })
        for i, (_, width, kind) in enumerate(columns):
            ws.set_column(i, i, width)
        ws.set_row(top, 32)
        formats = [self.kind(kind) for _, _, kind in columns]
        for r, values in enumerate(data):
            for c, value in enumerate(values):
                self.put(ws, top + 1 + r, c, value, formats[c])
        total_row = None
        if totals and rows:
            total_row = last + 1
            ws.write_string(total_row - 1, 0, total_label, self.fmt(bold=True, top=2, top_color=INK))
            for c in range(1, len(columns)):
                kind = columns[c][2]
                style = self.kind(kind if kind in ('money', 'int', 'pct', 'change') else 'text', bold=True, top=2, top_color=INK)
                spec = totals.get(c)
                if spec is None:
                    ws.write_blank(total_row - 1, c, None, style)
                    continue
                if spec == 'sum':
                    letter = xlsxwriter.utility.xl_col_to_name(c)
                    total = sum(float(v.value if isinstance(v, F) else v or 0) for v in (row[c] for row in rows))
                    spec = F(f'=SUBTOTAL(109,{letter}{first}:{letter}{last})', total)
                else:
                    spec = spec(first, last)
                self.put(ws, total_row - 1, c, spec, style)
        ws.freeze_panes(top + 1, 1)
        ws.repeat_rows(top)
        return first, last, total_row

    def status_colors(self, ws, cell_range, labels):
        """Подсветить статусы: {текст: (цвет текста, фон)}"""
        for text, (ink, bg) in labels.items():
            ws.conditional_format(cell_range, {'type': 'cell', 'criteria': '==', 'value': f'"{text}"',
                                               'format': self.wb.add_format({'font_color': ink, 'bg_color': bg, 'bold': True})})

    def tile(self, ws, row, col, label, value, kind, note=None):
        """Плитка сводки: подпись, крупное число (формула) и пояснение — на 2 колонки"""
        box = {'bg_color': SOFT}
        ws.merge_range(row, col, row, col + 1, label, self.fmt(font_size=9, font_color=MUTED, bold=True, top=2, top_color=ORANGE, **box))
        ws.merge_range(row + 1, col, row + 1, col + 1, '', self.kind(kind, font_size=17, bold=True, align='left', **box))
        self.put(ws, row + 1, col, value, self.kind(kind, font_size=17, bold=True, align='left', **box))
        ws.merge_range(row + 2, col, row + 2, col + 1, note or '', self.fmt(font_size=8, font_color=MUTED, text_wrap=True, **box))

    def chart(self, kind, title, series, y_format='#,##0', y_max=None, legend=True, width=560, height=290, subtype=None, date_axis=False):
        chart = self.wb.add_chart({'type': kind, **({'subtype': subtype} if subtype else {})})
        for s in series:
            chart.add_series(s)
        axis_font = {'name': FONT, 'size': 9, 'color': MUTED}
        chart.set_title({'name': title, 'overlay': False, 'name_font': {'name': FONT, 'size': 11, 'bold': True, 'color': INK}})
        if kind not in ('pie', 'doughnut'):
            category = {'num_font': axis_font, 'line': {'color': LINE}, 'major_tick_mark': 'none'}
            if date_axis:
                category['num_format'] = MONTH_SHORT
            value = {'num_font': axis_font, 'num_format': y_format, 'line': {'none': True}, 'major_tick_mark': 'none',
                     'major_gridlines': {'visible': True, 'line': {'color': '#ECEAE6', 'width': 0.75}}, 'min': 0}
            if y_max is not None:
                value['max'] = y_max
            if kind == 'bar':   # горизонтальные: первая строка — сверху; ось значений не нужна — подписи у столбиков
                chart.set_x_axis({'visible': False, 'major_gridlines': {'visible': False}, 'min': 0})
                chart.set_y_axis({**category, 'reverse': True})
            else:
                chart.set_x_axis(category)
                chart.set_y_axis(value)
        chart.set_legend({'position': 'bottom', 'font': axis_font} if legend else {'none': True})
        chart.set_chartarea({'border': {'none': True}, 'fill': {'color': '#FFFFFF'}})
        chart.set_plotarea({'border': {'none': True}})
        chart.set_size({'width': width, 'height': height})
        return chart

    def guide(self, ws, row, lines):
        ws.write_string(row, 0, 'Как устроен файл', self.fmt(bold=True, font_size=11))
        for i, line in enumerate(lines, 1):
            ws.write_string(row + i, 0, f'•  {line}', self.fmt(font_color=INK_2))

    def close(self):
        self.wb.close()
        return self.buffer.getvalue()


def bar_series(book, name, sheet, first, last, cat_col, val_col, color, labels=False, fmt='#,##0'):
    series = {'name': name, 'categories': [sheet, first - 1, cat_col, last - 1, cat_col],
              'values': [sheet, first - 1, val_col, last - 1, val_col],
              'fill': {'color': color}, 'border': {'none': True}, 'gap': 70}
    if labels:
        series['data_labels'] = {'value': True, 'num_format': fmt, 'font': {'name': FONT, 'size': 8, 'color': INK_2}}
    return series


def line_series(name, sheet, first, last, cat_col, val_col, color):
    return {'name': name, 'categories': [sheet, first - 1, cat_col, last - 1, cat_col],
            'values': [sheet, first - 1, val_col, last - 1, val_col],
            'line': {'color': color, 'width': 2.25},
            'marker': {'type': 'circle', 'size': 7, 'fill': {'color': color}, 'border': {'color': '#FFFFFF'}},
            'data_labels': {'value': True, 'position': 'above', 'num_format': PCT, 'font': {'name': FONT, 'size': 8, 'color': INK_2}}}


def response(content, filename):
    from django.http import HttpResponse
    resp = HttpResponse(content, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


# ======================================================================
# Статистика доходов
# ======================================================================

def income_workbook(months, group_type):
    from .views import MONTHS_RU, get_current_period, income_stats
    rows = []
    stats = income_stats(months, group_type, rows_out=rows)
    cur_y, cur_m = get_current_period()
    current = date(cur_y, cur_m, 1)
    now = timezone.localtime()

    groups = {r['group'].id: r['group'] for r in rows}
    for x in stats['groups']:
        groups.setdefault(x['group'].id, x['group'])
    group_label = unique_labels(groups.values(), lambda g: g.name)
    students = {r['student'].id: r['student'] for r in rows}
    student_label = unique_labels(students.values(), lambda s: s.name)

    def teacher_of(group):
        return group.teacher.username if group.teacher else 'без учителя'

    def status_of(r):
        if r['is_paid']:
            return PAID
        if r['is_current']:
            return WAITING
        return DEBT if r['amount'] else NO_AMOUNT

    rows.sort(key=lambda r: (r['cycle'], group_label[r['group'].id].lower(), student_label[r['student'].id].lower()))
    type_label = {'all': 'группы и индивидуальные', 'group': 'только группы', 'individual': 'только индивидуальные'}[group_type]
    chart_months = stats['chart']
    first_month = MONTHS_RU[chart_months[0]['month'] - 1] + f' {chart_months[0]["year"]}'
    last_month = MONTHS_RU[chart_months[-1]['month'] - 1] + f' {chart_months[-1]["year"]}'

    book = Book('Статистика доходов — Enjoy English', f'{first_month} — {last_month}')
    S, M, G, T, D, N = 'Сводка', 'По месяцам', 'По группам', 'По учителям', 'Должники', 'Начисления'
    ws_s = book.sheet(S, ORANGE)
    ws_m, ws_g, ws_t, ws_d, ws_n = (book.sheet(name) for name in (M, G, T, D, N))
    cur_cell = f"{quote(S)}!$C$4"

    # ---------- Начисления: каждая строка — ученик × группа × месяц ----------
    book.title(ws_n, 'Начисления — ученик × группа × месяц',
               'Из этого листа считаются все остальные. Поменяйте «Статус» на «Оплачено» или «Долг» — пересчитаются итоги и сводка.')
    n_top = 3
    n_first, n_last = n_top + 2, n_top + 1 + max(1, len(rows))
    data = []
    for i, r in enumerate(rows):
        excel_row = n_first + i
        amount = float(r['amount'])
        status = status_of(r)
        payment = r['payment']
        data.append([
            date(r['year'], r['month'], 1), group_label[r['group'].id], r['group'].get_group_type_display(),
            teacher_of(r['group']), student_label[r['student'].id], r['lessons'], float(r['price']),
            F(f'=F{excel_row}*G{excel_row}', amount) if r['formula_ok'] else amount,
            status, ('Наличные' if r['method'] == 'cash' else 'Банк') if r['is_paid'] else '',
            F(f'=IF(I{excel_row}="{PAID}",H{excel_row},0)', amount if status == PAID else 0),
            F(f'=IF(I{excel_row}="{DEBT}",H{excel_row},0)', amount if status == DEBT else 0),
            timezone.localtime(payment.paid_at).date() if r['is_paid'] and payment and payment.paid_at else '',
            payment.marked_by.username if r['is_paid'] and payment and payment.marked_by else '',
            'ушёл из группы, оплата сохранена' if r['left'] else '',
        ])
    book.table(ws_n, n_top, [
        ('Месяц', 15, 'month'), ('Группа', 28, 'text'), ('Тип', 15, 'muted'), ('Учитель', 14, 'text'),
        ('Ученик', 24, 'name'), ('Уроков', 8, 'int'), ('Цена урока', 12, 'money'), ('К оплате', 13, 'money'),
        ('Статус', 13, 'center'), ('Способ', 11, 'center'), ('Оплачено', 13, 'money'), ('Долг', 12, 'money'),
        ('Дата оплаты', 12, 'date'), ('Кто отметил', 13, 'muted'), ('Примечание', 30, 'muted'),
    ], data, totals={7: 'sum', 10: 'sum', 11: 'sum'})
    book.status_colors(ws_n, f'I{n_first}:I{n_last}', {PAID: (GOOD, GOOD_BG), DEBT: (BAD, BAD_BG), WAITING: ('#9A6700', WARN_BG)})
    ws_n.data_validation(f'I{n_first}:I{n_last}', {'validate': 'list', 'source': [PAID, DEBT, WAITING, NO_AMOUNT]})
    col = {name: col_range(N, letter, n_first, n_last) for name, letter in
           (('month', 'A'), ('group', 'B'), ('teacher', 'D'), ('student', 'E'), ('due', 'H'),
            ('status', 'I'), ('method', 'J'), ('paid', 'K'), ('debt', 'L'))}

    def rows_where(**cond):
        return [r for r in rows if all(cond_ok(r, k, v) for k, v in cond.items())]

    def cond_ok(r, key, value):
        if key == 'month':
            return date(r['year'], r['month'], 1) == value
        if key == 'before':
            return date(r['year'], r['month'], 1) < value
        if key == 'group':
            return r['group'].id == value
        if key == 'teacher':
            return teacher_of(r['group']) == value
        if key == 'student':
            return r['student'].id == value
        if key == 'status':
            return status_of(r) == value
        if key == 'method':
            return r['is_paid'] and (r['method'] == 'cash') == (value == 'Наличные')
        raise KeyError(key)

    def total(items, what):
        if what == 'due':
            return sum(float(r['amount']) for r in items)
        if what == 'paid':
            return sum(float(r['amount']) for r in items if status_of(r) == PAID)
        return sum(float(r['amount']) for r in items if status_of(r) == DEBT)

    # ---------- По месяцам ----------
    book.title(ws_m, 'Доход по месяцам', 'Считается из листа «Начисления». Текущий месяц ещё идёт — его долг пока не долг.')
    m_top = 3
    data = []
    for i, c in enumerate(chart_months):
        r = m_top + 2 + i
        month = date(c['year'], c['month'], 1)
        items = rows_where(month=month)
        due, paid = total(items, 'due'), total(items, 'paid')
        prev_paid = total(rows_where(month=date(chart_months[i - 1]['year'], chart_months[i - 1]['month'], 1)), 'paid') if i else 0
        data.append([
            month,
            F(f'=SUMIFS({col["due"]},{col["month"]},A{r})', due),
            F(f'=SUMIFS({col["paid"]},{col["month"]},A{r})', paid),
            F(f'=SUMIFS({col["paid"]},{col["month"]},A{r},{col["method"]},"Банк")', total(rows_where(month=month, method='Банк'), 'paid')),
            F(f'=SUMIFS({col["paid"]},{col["month"]},A{r},{col["method"]},"Наличные")', total(rows_where(month=month, method='Наличные'), 'paid')),
            F(f'=IFERROR(C{r}/B{r},0)', ratio(paid, due)),
            F(f'=COUNTIFS({col["month"]},A{r},{col["status"]},"{PAID}")', len(rows_where(month=month, status=PAID))),
            F(f'=COUNTIFS({col["month"]},A{r})', len(items)),
            F(f'=SUMIFS({col["debt"]},{col["month"]},A{r})', total(items, 'debt')),
            F(f'=IFERROR(C{r}/C{r - 1}-1,"")', ratio(paid, prev_paid) - 1 if prev_paid else '') if i else '',
            'идёт' if c['is_current'] else 'закрыт',
        ])
    m_first, m_last, m_total = book.table(ws_m, m_top, [
        ('Месяц', 16, 'month'), ('Ожидалось', 14, 'money'), ('Собрано', 14, 'money'), ('Банк', 13, 'money'),
        ('Наличные', 13, 'money'), ('Собрано, %', 11, 'pct'), ('Оплатили', 10, 'int'), ('Начислений', 11, 'int'),
        ('Долг', 13, 'money'), ('К прошлому месяцу', 12, 'change'), ('Статус месяца', 10, 'muted'),
    ], data, totals={1: 'sum', 2: 'sum', 3: 'sum', 4: 'sum', 6: 'sum', 8: 'sum',
                     5: lambda a, b: F(f'=IFERROR(C{b + 1}/B{b + 1},0)', ratio(total(rows, 'paid'), total(rows, 'due')))})
    ws_m.conditional_format(f'F{m_first}:F{m_last}', {'type': '3_color_scale', 'min_color': '#F8D7D7', 'mid_color': '#FFF3D6', 'max_color': '#D9F2D9',
                                                      'min_type': 'num', 'min_value': 0.5, 'mid_type': 'num', 'mid_value': 0.8, 'max_type': 'num', 'max_value': 1})
    ws_m.conditional_format(f'J{m_first}:J{m_last}', {'type': 'cell', 'criteria': '<', 'value': 0, 'format': book.wb.add_format({'font_color': BAD})})
    ws_m.conditional_format(f'J{m_first}:J{m_last}', {'type': 'cell', 'criteria': '>', 'value': 0, 'format': book.wb.add_format({'font_color': GOOD})})

    # ---------- По группам ----------
    book.title(ws_g, 'Доход по группам', '«Собрано вовремя» — по закрытым месяцам (текущий ещё идёт). Сортировка — стрелкой в заголовке.')
    g_top = 3
    g_rows = len(stats['groups'])
    g_first, g_last = g_top + 2, g_top + 1 + max(1, g_rows)
    all_paid = total(rows, 'paid')
    data = []
    for i, x in enumerate(stats['groups']):
        r = g_first + i
        g = x['group']
        items = rows_where(group=g.id)
        done = rows_where(group=g.id, before=current)
        paid, done_due = total(items, 'paid'), total(done, 'due')
        paid_count = len(rows_where(group=g.id, status=PAID))
        data.append([
            group_label[g.id], g.get_group_type_display(), teacher_of(g), x['students'],
            F(f'=SUMIFS({col["due"]},{col["group"]},A{r})', total(items, 'due')),
            F(f'=SUMIFS({col["paid"]},{col["group"]},A{r})', paid),
            F(f'=IFERROR(F{r}/SUM($F${g_first}:$F${g_last}),0)', ratio(paid, all_paid)),
            F(f'=IFERROR(SUMIFS({col["paid"]},{col["group"]},A{r},{col["month"]},"<"&{cur_cell})'
              f'/SUMIFS({col["due"]},{col["group"]},A{r},{col["month"]},"<"&{cur_cell}),"")',
              ratio(total(done, 'paid'), done_due) if done_due else ''),
            F(f'=SUMIFS({col["debt"]},{col["group"]},A{r})', total(items, 'debt')),
            F(f'=IFERROR(F{r}/COUNTIFS({col["group"]},A{r},{col["status"]},"{PAID}"),"")', paid / paid_count if paid_count else ''),
        ])
    book.table(ws_g, g_top, [
        ('Группа', 30, 'name'), ('Тип', 15, 'muted'), ('Учитель', 14, 'text'), ('Учеников', 10, 'int'),
        ('Ожидалось', 14, 'money'), ('Собрано', 14, 'money'), ('Доля дохода', 11, 'pct'),
        ('Собрано вовремя', 12, 'pct'), ('Долг', 13, 'money'), ('Средний чек', 13, 'money'),
    ], data, totals={3: 'sum', 4: 'sum', 5: 'sum', 8: 'sum', 6: lambda a, b: F(f'=SUM(G{a}:G{b})', 1 if all_paid else 0)})
    ws_g.conditional_format(f'F{g_first}:F{g_last}', {'type': 'data_bar', 'bar_color': ORANGE_TINT, 'bar_solid': True, 'bar_no_border': True})
    ws_g.conditional_format(f'H{g_first}:H{g_last}', {'type': '3_color_scale', 'min_color': '#F8D7D7', 'mid_color': '#FFF3D6', 'max_color': '#D9F2D9',
                                                      'min_type': 'num', 'min_value': 0.5, 'mid_type': 'num', 'mid_value': 0.8, 'max_type': 'num', 'max_value': 1})

    # ---------- По учителям ----------
    book.title(ws_t, 'Доход по учителям', 'Деньги групп, где учитель — основной.')
    t_top = 3
    teachers = stats['teachers']
    t_first, t_last = t_top + 2, t_top + 1 + max(1, len(teachers))
    data = []
    for i, t in enumerate(teachers):
        r = t_first + i
        name = t['name'] or 'без учителя'
        items = rows_where(teacher=name)
        paid = total(items, 'paid')
        data.append([
            name, t['groups'], t['students'],
            F(f'=SUMIFS({col["paid"]},{col["teacher"]},A{r})', paid),
            F(f'=IFERROR(D{r}/SUM($D${t_first}:$D${t_last}),0)', ratio(paid, all_paid)),
            F(f'=SUMIFS({col["debt"]},{col["teacher"]},A{r})', total(items, 'debt')),
        ])
    book.table(ws_t, t_top, [
        ('Учитель', 20, 'name'), ('Групп', 9, 'int'), ('Учеников', 10, 'int'), ('Собрано', 14, 'money'),
        ('Доля дохода', 11, 'pct'), ('Долг', 13, 'money'),
    ], data, totals={1: 'sum', 2: 'sum', 3: 'sum', 5: 'sum', 4: lambda a, b: F(f'=SUM(E{a}:E{b})', 1 if all_paid else 0)})
    ws_t.conditional_format(f'D{t_first}:D{t_last}', {'type': 'data_bar', 'bar_color': BLUE_TINT, 'bar_solid': True, 'bar_no_border': True})

    # ---------- Должники ----------
    book.title(ws_d, 'Должники за период', 'Не оплачены закрытые месяцы. Позвоните родителям — телефоны рядом.')
    d_top = 3
    debtors = stats['debtors']
    d_first, d_last = d_top + 2, d_top + 1 + max(1, len(debtors))
    data = []
    for i, d in enumerate(debtors):
        r = d_first + i
        st = d['student']
        data.append([
            student_label[st.id], st.phone or '', st.parent_phone or '', ', '.join(d['groups']),
            ', '.join(f'{MONTHS_RU[x["month"] - 1].lower()} {x["year"]}' for x in d['months']),
            F(f'=COUNTIFS({col["student"]},A{r},{col["status"]},"{DEBT}")', len(rows_where(student=st.id, status=DEBT))),
            F(f'=SUMIFS({col["debt"]},{col["student"]},A{r})', total(rows_where(student=st.id), 'debt')),
        ])
    book.table(ws_d, d_top, [
        ('Ученик', 24, 'name'), ('Телефон', 16, 'text'), ('Телефон родителя', 16, 'text'), ('Группы', 26, 'wrap'),
        ('Месяцы', 30, 'wrap'), ('Месяцев', 9, 'int'), ('Долг', 13, 'money'),
    ], data, totals={5: 'sum', 6: 'sum'})
    ws_d.conditional_format(f'G{d_first}:G{d_last}', {'type': 'data_bar', 'bar_color': BAD_TINT, 'bar_solid': True, 'bar_no_border': True})

    # ---------- Сводка ----------
    ws_s.hide_gridlines(2)
    ws_s.set_column(0, 0, 2)
    for c in range(1, 13):
        ws_s.set_column(c, c, 12.5 if c % 3 else 2)
    ws_s.set_row(0, 30)
    ws_s.write_string(0, 1, 'Enjoy English — статистика доходов', book.fmt(bold=True, font_size=18))
    ws_s.write_string(1, 1, f'{first_month} — {last_month} · {type_label} · выгружено {now:%d.%m.%Y %H:%M}',
                      book.fmt(font_color=MUTED, font_size=9))
    ws_s.write_string(3, 1, 'Текущий месяц', book.fmt(font_color=MUTED, font_size=9))
    ws_s.write_datetime(3, 2, current, book.kind('month', bold=True))
    ws_s.write_string(3, 4, '← идёт, в «вовремя» и «долг» не входит', book.fmt(font_color=MUTED, font_size=9))

    done_all = rows_where(before=current)
    done_months = [c for c in chart_months if date(c['year'], c['month'], 1) < current]
    paid_all, due_all = total(rows, 'paid'), total(rows, 'due')
    cash_all = total(rows_where(method='Наличные'), 'paid')
    best = max(chart_months, key=lambda c: total(rows_where(month=date(c['year'], c['month'], 1)), 'paid')) if rows else None
    mrange = lambda letter: col_range(M, letter, m_first, m_last)  # noqa: E731
    tiles = [
        ('Собрано за период', F(f'=SUM({col["paid"]})', paid_all), 'money', 'все оплаты, включая текущий месяц'),
        ('Ожидалось', F(f'=SUM({col["due"]})', due_all), 'money', 'цена урока × уроки по расписанию'),
        ('Собрано вовремя', F(f'=IFERROR(SUMIFS({col["paid"]},{col["month"]},"<"&$C$4)/SUMIFS({col["due"]},{col["month"]},"<"&$C$4),0)',
                              ratio(total(done_all, 'paid'), total(done_all, 'due'))), 'pct', 'доля оплаченного в закрытых месяцах'),
        ('Долг', F(f'=SUM({col["debt"]})', total(rows, 'debt')), 'money', 'не оплачено в закрытых месяцах'),
        ('В среднем за месяц', F(f'=IFERROR(SUMIFS({col["paid"]},{col["month"]},"<"&$C$4)/COUNTIFS({mrange("A")},"<"&$C$4),0)',
                                 total(done_all, 'paid') / len(done_months) if done_months else 0), 'money', 'по закрытым месяцам'),
        ('Лучший месяц', F(f'=IF(MAX({mrange("C")})=0,"",IFERROR(INDEX({mrange("A")},MATCH(MAX({mrange("C")}),{mrange("C")},0)),""))',
                           excel_date(date(best['year'], best['month'], 1)) if best and paid_all else ''), 'month', 'больше всего собрано'),
        ('Наличными', F(f'=IFERROR(SUMIFS({col["paid"]},{col["method"]},"Наличные")/SUM({col["paid"]}),0)', ratio(cash_all, paid_all)), 'pct',
         'остальное — банком'),
        ('Должников', F(f'=COUNTA({col_range(D, "A", d_first, d_last)})', len(debtors)), 'int', 'учеников с долгом'),
    ]
    for i, (label, value, kind, note) in enumerate(tiles):
        book.tile(ws_s, 5 + (i // 4) * 4, 1 + (i % 4) * 3, label, value, kind, note)

    # Графики: два широких по месяцам, ниже — способы оплаты и группы
    if chart_months:
        chart = book.chart('column', 'Ожидалось и собрано по месяцам, ₸', [
            bar_series(book, 'Ожидалось', M, m_first, m_last, 0, 1, BLUE),
            bar_series(book, 'Собрано', M, m_first, m_last, 0, 2, ORANGE),
        ], date_axis=True, width=790, height=300)
        ws_s.insert_chart(13, 1, chart)
        chart = book.chart('line', 'Собрано, % от ожидаемого', [line_series('Собрано, %', M, m_first, m_last, 0, 5, BLUE)],
                           y_format=PCT, y_max=1, legend=False, date_axis=True, width=790, height=250)
        ws_s.insert_chart(29, 1, chart)
    # Цифры для круговой диаграммы — под ней
    ws_s.write_string(57, 1, 'Банк', book.fmt(font_color=INK_2))
    book.put(ws_s, 57, 2, F(f'=SUMIFS({col["paid"]},{col["method"]},"Банк")', paid_all - cash_all), book.kind('money'))
    ws_s.write_string(58, 1, 'Наличные', book.fmt(font_color=INK_2))
    book.put(ws_s, 58, 2, F(f'=SUMIFS({col["paid"]},{col["method"]},"Наличные")', cash_all), book.kind('money'))
    if paid_all:
        chart = book.chart('doughnut', 'Как платят: банк и наличные', [{
            'name': 'Способ оплаты', 'categories': [S, 57, 1, 58, 1], 'values': [S, 57, 2, 58, 2],
            'points': [{'fill': {'color': BLUE}, 'border': {'color': '#FFFFFF', 'width': 2}},
                       {'fill': {'color': ORANGE}, 'border': {'color': '#FFFFFF', 'width': 2}}],
            'data_labels': {'percentage': True, 'font': {'name': FONT, 'size': 9, 'color': '#FFFFFF', 'bold': True}},
        }], width=380, height=290)
        chart.set_hole_size(55)
        ws_s.insert_chart(42, 1, chart)
    group_chart_height = max(290, 34 * min(g_rows, 10) + 80)
    if g_rows:
        top_groups = min(g_rows, 10)
        chart = book.chart('bar', 'Доход по группам, ₸' + (' — топ-10' if g_rows > 10 else ''),
                           [bar_series(book, 'Собрано', G, g_first, g_first + top_groups - 1, 0, 5, ORANGE, labels=True)],
                           legend=False, width=400, height=group_chart_height)
        ws_s.insert_chart(42, 6, chart, {'x_offset': 8})
    guide_row = max(61, 42 + group_chart_height // 20 + 2)
    ws_s.set_h_pagebreaks([29, 41])
    book.guide(ws_s, guide_row, [
        '«Начисления» — каждая строка: ученик × группа × месяц. Все остальные листы и эта сводка считаются из него формулами.',
        'Поменяли «Статус» на «Оплачено» или «Долг» — пересчитаются итоги, проценты и графики.',
        'Фильтр и сортировка — стрелки в заголовках таблиц. Строка «Итого» считает только видимые строки.',
        'Текущий месяц ещё идёт: его неоплаты — «Ждём оплату», а не долг. «Собрано вовремя» — только по закрытым месяцам.',
    ])
    ws_s.activate()
    return book.close(), f'enjoy-english-income-{months}m-{now:%Y-%m-%d}.xlsx'


# ======================================================================
# Общий отчёт
# ======================================================================

def general_workbook():
    from .models import Attendance, Enrollment, Group, Payment, Student
    from .views import (MONTHS_RU, count_lessons_in_period, get_current_period, get_period, homework_counts, is_enrolled_by)
    now = timezone.localtime()
    today = now.date()

    # Последние 12 месяцев оплаты — до самого позднего текущего периода среди групп
    groups = list(Group.objects.filter(is_active=True).select_related('teacher')
                  .prefetch_related('teachers', 'schedule_slots', 'enrollments__student'))
    groups.sort(key=lambda g: (g.group_type == 'individual', g.name.lower()))
    group_by_id = {g.id: g for g in groups}
    current_by_group = {g.id: get_current_period(g.cycle_start_day) for g in groups}
    last_y, last_m = max(current_by_group.values(), default=get_current_period())
    months, y, m = [], last_y, last_m
    for _ in range(12):
        months.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    months.reverse()

    students = list(Student.objects.all().order_by('name'))
    student_label = unique_labels(students, lambda s: s.name)
    group_label = unique_labels(groups, lambda g: g.name)
    active_groups = {g.id for g in groups}
    enrollments = [e for g in groups for e in g.enrollments.all() if e.student.is_active]
    enrollments.sort(key=lambda e: (student_label[e.student_id].lower(), group_label[e.group_id].lower()))
    by_student = {}
    for e in enrollments:
        by_student.setdefault(e.student_id, []).append(group_label[e.group_id])

    payments = {(p.student_id, p.group_id, p.cycle_number): p for p in Payment.objects.filter(
        cycle_number__in=[y * 100 + m for y, m in months], group_id__in=active_groups)}

    # Журнал посещаемости за 12 месяцев (отменённые уроки не считаются)
    since = date(months[0][0], months[0][1], 1)
    log = list(Attendance.objects.filter(lesson__date__gte=since, lesson__is_cancelled=False)
               .select_related('lesson', 'lesson__teacher', 'lesson__group', 'lesson__group__teacher', 'student')
               .order_by('-lesson__date', 'lesson__group__name', 'student__name'))
    all_groups_label = dict(group_label)
    extra = {a.lesson.group for a in log if a.lesson.group_id not in all_groups_label}
    all_groups_label.update({g.id: f'{g.name} (архив)' for g in extra})
    status_label = {'present': PRESENT, 'late': LATE}

    book = Book('Общий отчёт — Enjoy English', f'на {today:%d.%m.%Y}')
    S, U, P, G, A = 'Сводка', 'Ученики', 'Оплаты', 'Группы', 'Посещаемость'
    ws_s = book.sheet(S, ORANGE)
    ws_u, ws_p, ws_g, ws_a = (book.sheet(name) for name in (U, P, G, A))

    # ---------- Посещаемость (журнал) ----------
    book.title(ws_a, 'Посещаемость — журнал за 12 месяцев', 'Каждая строка — отметка ученика на уроке. Из журнала считаются проценты на других листах.')
    a_top = 3
    a_first, a_last = a_top + 2, a_top + 1 + max(1, len(log))
    data = []
    for a in log:
        lesson = a.lesson
        # Учитель урока (в группе с двумя учителями у каждого свой урок), у старых уроков — учитель группы
        teacher = lesson.teacher or lesson.group.teacher
        data.append([
            lesson.date, date(lesson.date.year, lesson.date.month, 1), all_groups_label[lesson.group_id],
            teacher.username if teacher else '', student_label.get(a.student_id, a.student.name),
            # ДЗ: «—» — не входит в процент (до 1 октября 2026 или домашку не задавали)
            status_label.get(a.status, ABSENT), ('Да' if a.homework_done else 'Нет') if homework_counts(lesson) else '—',
            a.stars or 0, lesson.topic or '',
        ])
    book.table(ws_a, a_top, [
        ('Дата', 12, 'date'), ('Месяц', 14, 'month'), ('Группа', 28, 'text'), ('Учитель', 13, 'muted'),
        ('Ученик', 24, 'name'), ('Статус', 11, 'center'), ('ДЗ', 7, 'center'), ('Звёзды', 8, 'int'), ('Тема урока', 30, 'muted'),
    ], data)
    book.status_colors(ws_a, f'F{a_first}:F{a_last}', {PRESENT: (GOOD, GOOD_BG), LATE: ('#9A6700', WARN_BG), ABSENT: (BAD, BAD_BG)})
    acol = {name: col_range(A, letter, a_first, a_last) for name, letter in
            (('month', 'B'), ('group', 'C'), ('student', 'E'), ('status', 'F'), ('hw', 'G'), ('stars', 'H'))}

    def log_count(pred):
        return sum(1 for a in log if pred(a))

    # ---------- Ученики ----------
    book.title(ws_u, 'Ученики', 'Посещаемость, ДЗ и звёзды — за 12 месяцев, из листа «Посещаемость». В архиве — ушедшие.')
    u_top = 3
    u_first, u_last = u_top + 2, u_top + 1 + max(1, len(students))
    data = []
    for i, st in enumerate(students):
        r = u_first + i
        marks = [a for a in log if a.student_id == st.id]
        came = sum(1 for a in marks if a.status in status_label)
        hw_marks = [a for a in marks if homework_counts(a.lesson)]
        hw = sum(1 for a in hw_marks if a.homework_done)
        data.append([
            student_label[st.id], 'Учится' if st.is_active else 'В архиве', st.age or '', st.school or '', st.grade or '',
            st.phone or '', st.parent_name or '', st.parent_phone or '', ', '.join(by_student.get(st.id, [])) if st.is_active else '',
            timezone.localtime(st.created_at).date() if st.created_at else '', float(st.price_per_lesson or 0),
            F(f'=COUNTIFS({acol["student"]},A{r})', len(marks)),
            F(f'=COUNTIFS({acol["student"]},A{r},{acol["status"]},"{PRESENT}")+COUNTIFS({acol["student"]},A{r},{acol["status"]},"{LATE}")', came),
            F(f'=IFERROR(M{r}/L{r},"")', ratio(came, len(marks)) if marks else ''),
            F(f'=IFERROR(COUNTIFS({acol["student"]},A{r},{acol["hw"]},"Да")/(COUNTIFS({acol["student"]},A{r},{acol["hw"]},"Да")'
              f'+COUNTIFS({acol["student"]},A{r},{acol["hw"]},"Нет")),"")', ratio(hw, len(hw_marks)) if hw_marks else ''),
            F(f'=SUMIFS({acol["stars"]},{acol["student"]},A{r})', sum(a.stars or 0 for a in marks)),
        ])
    book.table(ws_u, u_top, [
        ('Ученик', 24, 'name'), ('Статус', 10, 'center'), ('Возраст', 8, 'int'), ('Школа', 16, 'text'), ('Класс', 7, 'center'),
        ('Телефон', 15, 'text'), ('Родитель', 16, 'text'), ('Телефон родителя', 15, 'text'), ('Группы', 26, 'wrap'),
        ('Добавлен', 11, 'date'), ('Цена урока', 11, 'money'), ('Отметок', 9, 'int'), ('Был', 7, 'int'),
        ('Посещаемость', 13, 'pct'), ('ДЗ сдано', 10, 'pct'), ('Звёзды', 8, 'int'),
    ], data)
    book.status_colors(ws_u, f'B{u_first}:B{u_last}', {'Учится': (GOOD, GOOD_BG), 'В архиве': (MUTED, SOFT)})
    for letter in ('N', 'O'):
        ws_u.conditional_format(f'{letter}{u_first}:{letter}{u_last}', {
            'type': '3_color_scale', 'min_color': '#F8D7D7', 'mid_color': '#FFF3D6', 'max_color': '#D9F2D9',
            'min_type': 'num', 'min_value': 0.5, 'mid_type': 'num', 'mid_value': 0.75, 'max_type': 'num', 'max_value': 1})
    ws_u.conditional_format(f'P{u_first}:P{u_last}', {'type': 'data_bar', 'bar_color': WARN_TINT, 'bar_solid': True, 'bar_no_border': True})

    # ---------- Оплаты (ученик × группа, 12 месяцев) ----------
    book.title(ws_p, 'Оплаты по месяцам', f'Текущий месяц — по каждой группе свой (по дню начала цикла). {DEBT} — закрытый месяц не оплачен, '
                                          f'«{WAITING}» — текущий, «—» — ещё не учился.')
    p_top = 3
    p_first, p_last = p_top + 2, p_top + 1 + max(1, len(enrollments))
    month_cols = 7
    data = []
    status_rows = []
    for i, e in enumerate(enrollments):
        r = p_first + i
        group = group_by_id[e.group_id]
        cur_y, cur_m = current_by_group[group.id]
        start, end = get_period(cur_y, cur_m, group.cycle_start_day)
        lessons = count_lessons_in_period(group, start, end)
        price = float(e.student.price_per_lesson or 0)

        def status(y, m):
            p = payments.get((e.student_id, group.id, y * 100 + m))
            if p and p.is_paid:
                return PAID
            if p and p.is_partial:
                return PARTIAL
            if (y, m) > (cur_y, cur_m):
                return ''
            if not is_enrolled_by(e, get_period(y, m, group.cycle_start_day)[1]):
                return '—'
            return WAITING if (y, m) == (cur_y, cur_m) else DEBT
        statuses = [status(y, m) for y, m in months]
        status_rows.append((statuses, status(cur_y, cur_m), price * lessons))
        first_l = xlsxwriter.utility.xl_col_to_name(month_cols)
        last_l = xlsxwriter.utility.xl_col_to_name(month_cols + len(months) - 1)
        data.append([
            student_label[e.student_id], group_label[group.id], group.teacher.username if group.teacher else '',
            price, lessons, F(f'=D{r}*E{r}', price * lessons), status(cur_y, cur_m),
            *statuses,
            F(f'=COUNTIF({first_l}{r}:{last_l}{r},"{DEBT}")', statuses.count(DEBT)),
        ])
    columns = [('Ученик', 24, 'name'), ('Группа', 26, 'text'), ('Учитель', 12, 'muted'), ('Цена урока', 11, 'money'),
               ('Уроков в тек. месяце', 10, 'int'), ('К оплате сейчас', 13, 'money'), ('Текущий месяц', 12, 'center')]
    columns += [(f'{MONTHS_RU[m - 1][:3]} {y}', 9.5, 'center') for y, m in months]
    columns += [('Месяцев долга', 10, 'int')]
    book.table(ws_p, p_top, columns, data, totals={5: 'sum', len(columns) - 1: 'sum'})
    last_status_col = xlsxwriter.utility.xl_col_to_name(month_cols + len(months) - 1)
    book.status_colors(ws_p, f'G{p_first}:{last_status_col}{p_last}', {
        PAID: (GOOD, GOOD_BG), PARTIAL: ('#9A6700', WARN_BG), DEBT: (BAD, BAD_BG), WAITING: ('#9A6700', WARN_BG)})
    ws_p.freeze_panes(p_top + 1, 2)
    pcol = {name: col_range(P, letter, p_first, p_last) for name, letter in (('group', 'B'), ('due', 'F'), ('status', 'G'))}

    # ---------- Группы ----------
    book.title(ws_g, 'Группы', 'Ученики и оплата текущего месяца — из листа «Оплаты», посещаемость — из «Посещаемости» (12 месяцев).')
    g_top = 3
    g_first, g_last = g_top + 2, g_top + 1 + max(1, len(groups))
    data = []
    for i, g in enumerate(groups):
        r = g_first + i
        mine = [(s, cur, due) for e, (s, cur, due) in zip(enrollments, status_rows) if e.group_id == g.id]
        due = sum(x[2] for x in mine)
        paid = sum(x[2] for x in mine if x[1] == PAID)
        marks = [a for a in log if a.lesson.group_id == g.id]
        came = sum(1 for a in marks if a.status in status_label)
        data.append([
            group_label[g.id], g.get_group_type_display(), g.get_level_display() if g.level else '',
            g.teacher.username if g.teacher else '', ', '.join(t.username for t in g.teachers.all()),
            g.get_schedule_display(),
            F(f'=COUNTIFS({pcol["group"]},A{r})', len(mine)),
            F(f'=SUMIFS({pcol["due"]},{pcol["group"]},A{r})', due),
            F(f'=SUMIFS({pcol["due"]},{pcol["group"]},A{r},{pcol["status"]},"{PAID}")', paid),
            F(f'=IFERROR(I{r}/H{r},"")', ratio(paid, due) if due else ''),
            F(f'=IFERROR((COUNTIFS({acol["group"]},A{r},{acol["status"]},"{PRESENT}")+COUNTIFS({acol["group"]},A{r},{acol["status"]},"{LATE}"))'
              f'/COUNTIFS({acol["group"]},A{r}),"")', ratio(came, len(marks)) if marks else ''),
        ])
    book.table(ws_g, g_top, [
        ('Группа', 30, 'name'), ('Тип', 15, 'muted'), ('Уровень', 17, 'text'), ('Учитель', 13, 'text'), ('Доп. учителя', 14, 'muted'),
        ('Расписание', 30, 'wrap'), ('Учеников', 9, 'int'), ('К оплате сейчас', 14, 'money'), ('Оплачено', 13, 'money'),
        ('Оплачено, %', 11, 'pct'), ('Посещаемость', 13, 'pct'),
    ], data, totals={6: 'sum', 7: 'sum', 8: 'sum',
                     9: lambda a, b: F(f'=IFERROR(I{b + 1}/H{b + 1},0)', ratio(sum(x[2] for x in status_rows if x[1] == PAID), sum(x[2] for x in status_rows)))})
    for letter in ('J', 'K'):
        ws_g.conditional_format(f'{letter}{g_first}:{letter}{g_last}', {
            'type': '3_color_scale', 'min_color': '#F8D7D7', 'mid_color': '#FFF3D6', 'max_color': '#D9F2D9',
            'min_type': 'num', 'min_value': 0.5, 'mid_type': 'num', 'mid_value': 0.8, 'max_type': 'num', 'max_value': 1})
    ws_g.conditional_format(f'G{g_first}:G{g_last}', {'type': 'data_bar', 'bar_color': BLUE_TINT, 'bar_solid': True, 'bar_no_border': True})

    # ---------- Сводка ----------
    ws_s.hide_gridlines(2)
    ws_s.set_column(0, 0, 2)
    for c in range(1, 13):
        ws_s.set_column(c, c, 12.5 if c % 3 else 2)
    ws_s.set_row(0, 30)
    ws_s.write_string(0, 1, 'Enjoy English — общий отчёт', book.fmt(bold=True, font_size=18))
    ws_s.write_string(1, 1, f'на {today:%d.%m.%Y} · выгружено {now:%H:%M} · посещаемость и ДЗ — за 12 месяцев',
                      book.fmt(font_color=MUTED, font_size=9))
    ucol = {name: col_range(U, letter, u_first, u_last) for name, letter in (('status', 'B'), ('added', 'J'))}
    gcol_type = col_range(G, 'B', g_first, g_last)
    active = [s for s in students if s.is_active]
    due_now = sum(x[2] for x in status_rows)
    paid_now = sum(x[2] for x in status_rows if x[1] == PAID)
    came_all = log_count(lambda a: a.status in status_label)
    month_ago = today - timedelta(days=30)
    tiles = [
        ('Учатся сейчас', F(f'=COUNTIFS({ucol["status"]},"Учится")', len(active)), 'int', 'ученики не в архиве'),
        ('Новых за 30 дней', F(f'=COUNTIFS({ucol["added"]},">="&(TODAY()-30))',
                               sum(1 for s in students if s.created_at and timezone.localtime(s.created_at).date() >= month_ago)), 'int', 'добавлены в CRM'),
        ('Групп', F(f'=COUNTIFS({gcol_type},"Групповая")', sum(1 for g in groups if g.group_type != 'individual')), 'int', 'активные групповые'),
        ('Индивидуальных', F(f'=COUNTIFS({gcol_type},"Индивидуальная")', sum(1 for g in groups if g.group_type == 'individual')), 'int', 'занятия один на один'),
        ('К оплате в текущем месяце', F(f'=SUM({pcol["due"]})', due_now), 'money', 'цена урока × уроки по расписанию'),
        ('Уже оплачено', F(f'=SUMIFS({pcol["due"]},{pcol["status"]},"{PAID}")', paid_now), 'money', 'в текущем месяце'),
        ('Посещаемость', F(f'=IFERROR((COUNTIFS({acol["status"]},"{PRESENT}")+COUNTIFS({acol["status"]},"{LATE}"))/COUNTA({acol["status"]}),0)',
                           ratio(came_all, len(log))), 'pct', 'пришли на урок, за 12 месяцев'),
        ('ДЗ сдано', F(f'=IFERROR(COUNTIFS({acol["hw"]},"Да")/(COUNTIFS({acol["hw"]},"Да")+COUNTIFS({acol["hw"]},"Нет")),0)',
                       ratio(log_count(lambda a: a.homework_done and homework_counts(a.lesson)), log_count(lambda a: homework_counts(a.lesson)))),
         'pct', 'с 01.10.2026, где домашку задавали'),
    ]
    for i, (label, value, kind, note) in enumerate(tiles):
        book.tile(ws_s, 4 + (i // 4) * 4, 1 + (i % 4) * 3, label, value, kind, note)

    # Цифры для графиков: по месяцам — посещаемость и новые ученики (колонки B, C, E, F — широкие)
    h_top = 13
    ws_s.write_string(h_top - 1, 1, 'Цифры для графиков', book.fmt(bold=True, font_size=11))
    head = book.fmt(bold=True, font_size=9, font_color=MUTED, bottom=1, bottom_color=LINE)
    for c, text in ((1, 'Месяц'), (2, 'Отметок'), (4, 'Посещаемость'), (5, 'Новых учеников')):
        ws_s.write_string(h_top, c, text, head)
    month_starts = [date(y, m, 1) for y, m in months]
    for i, start in enumerate(month_starts):
        r = h_top + 2 + i
        marks = [a for a in log if date(a.lesson.date.year, a.lesson.date.month, 1) == start]
        came = sum(1 for a in marks if a.status in status_label)
        nxt = date(start.year + start.month // 12, start.month % 12 + 1, 1)
        added = sum(1 for s_ in students if s_.created_at and start <= timezone.localtime(s_.created_at).date() < nxt)
        book.put(ws_s, r - 1, 1, start, book.kind('month'))
        book.put(ws_s, r - 1, 2, F(f'=COUNTIFS({acol["month"]},B{r})', len(marks)), book.kind('int'))
        book.put(ws_s, r - 1, 4, F(f'=IFERROR((COUNTIFS({acol["month"]},B{r},{acol["status"]},"{PRESENT}")'
                                   f'+COUNTIFS({acol["month"]},B{r},{acol["status"]},"{LATE}"))/C{r},0)', ratio(came, len(marks))), book.kind('pct'))
        book.put(ws_s, r - 1, 5, F(f'=COUNTIFS({ucol["added"]},">="&B{r},{ucol["added"]},"<"&EDATE(B{r},1))', added), book.kind('int'))
    h_first, h_last = h_top + 2, h_top + 1 + len(month_starts)
    # Линия — с первого месяца, где уже отмечали посещаемость: пустые месяцы иначе выглядели бы как 0%
    marked = [i for i, start in enumerate(month_starts) if any(date(a.lesson.date.year, a.lesson.date.month, 1) == start for a in log)]
    line_first = h_first + (marked[0] if marked else 0)

    chart = book.chart('line', 'Посещаемость по месяцам', [line_series('Посещаемость', S, line_first, h_last, 1, 4, BLUE)],
                       y_format=PCT, y_max=1, legend=False, date_axis=True, width=390, height=280)
    ws_s.insert_chart(h_top - 1, 7, chart)
    chart = book.chart('column', 'Новые ученики по месяцам', [bar_series(book, 'Новых', S, h_first, h_last, 1, 5, AQUA, labels=True)],
                       legend=False, date_axis=True, width=390, height=280)
    ws_s.insert_chart(h_top + 15, 1, chart)

    # Оплата текущего месяца по статусам — цифры под диаграммой
    pay_top = h_top + 30
    states = [(PAID, GOOD), (PARTIAL, WARN), (WAITING, SERIOUS)]
    for i, (label, _) in enumerate(states):
        ws_s.write_string(pay_top + i, 7, label, book.fmt(font_color=INK_2))
        book.put(ws_s, pay_top + i, 8, F(f'=SUMIFS({pcol["due"]},{pcol["status"]},"{label}")',
                                          sum(x[2] for x in status_rows if x[1] == label)), book.kind('money'))
    if due_now:
        chart = book.chart('doughnut', 'Оплата текущего месяца, ₸', [{
            'name': 'Оплата', 'categories': [S, pay_top, 7, pay_top + 2, 7], 'values': [S, pay_top, 8, pay_top + 2, 8],
            'points': [{'fill': {'color': c}, 'border': {'color': '#FFFFFF', 'width': 2}} for _, c in states],
            'data_labels': {'percentage': True, 'font': {'name': FONT, 'size': 9, 'color': '#FFFFFF', 'bold': True}},
        }], width=390, height=280)
        chart.set_hole_size(55)
        ws_s.insert_chart(h_top + 15, 7, chart)

    group_rows = sum(1 for g in groups if g.group_type != 'individual')
    groups_top = pay_top + 5
    if group_rows:
        chart = book.chart('bar', 'Учеников по группам', [bar_series(book, 'Учеников', G, g_first, g_first + group_rows - 1, 0, 6, BLUE, labels=True)],
                           legend=False, width=790, height=max(240, 30 * group_rows + 80))
        ws_s.insert_chart(groups_top, 1, chart)
    ws_s.set_h_pagebreaks([h_top + 14, groups_top - 1])
    book.guide(ws_s, groups_top + max(240, 30 * group_rows + 80) // 20 + 2, [
        '«Посещаемость» — журнал отметок за 12 месяцев; проценты на листах «Ученики» и «Группы» считаются из него формулами.',
        '«Оплаты» — ученик × группа: статус каждого месяца и сумма к оплате сейчас (цена урока × уроки по расписанию).',
        'Фильтр и сортировка — стрелки в заголовках таблиц. Строка «Итого» считает только видимые строки.',
        'Цвета: зелёный — оплачено / был, жёлтый — частично или ждём / опоздал, красный — долг / не был.',
    ])
    ws_s.activate()
    return book.close(), f'enjoy-english-report-{today:%Y-%m-%d}.xlsx'
