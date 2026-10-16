"""Погода в Алматы — живой фон панелей приветствия и часов на главной.

Откуда данные: MET Norway (Норвежский метеорологический институт), api.met.no — прогноз
Locationforecast на ближайший час. Бесплатно, без ключа и регистрации, можно и для бизнеса;
условие — указать источник (лицензия CC BY 4.0), он написан в подсказке у погоды.
Правила API соблюдаем: браузеры туда сами не ходят — спрашивает сервер, с понятным User-Agent,
не чаще, чем разрешает их заголовок Expires (обычно раз в полчаса), и с If-Modified-Since.
Прогноз лежит в кэше на сутки вперёд: пропала связь — показываем нужный час из прошлого ответа.

Солнце и луна (высота над горизонтом, фаза луны, восход и закат) считаются здесь же
по астрономическим формулам, без интернета. Поэтому день, сумерки и ночь на фоне
меняются, даже если MET недоступен.

Посмотреть любую погоду: /dashboard/?weather=heavyrain&time=night
  weather — код MET (rain, lightsnow, fog, heavyrainandthunder…) или sun, clouds, cloudy, drizzle,
            rain, showers, heavyrain, storm, sleet, snow, heavysnow, fog;
  time    — day, morning, evening, dawn, dusk, night.
"""
import gzip
import json
import logging
import math
import time as clock
import urllib.error
import urllib.request
from datetime import datetime, time, timezone as dt_timezone
from email.utils import parsedate_to_datetime

from django.core.cache import cache
from django.http import JsonResponse
from django.utils import timezone

from .views import crm_view

log = logging.getLogger(__name__)

CITY = 'Алматы'
LAT, LON, ALTITUDE = 43.24, 76.91, 800   # центр города; больше 4 знаков после запятой API не принимает
API_URL = f'https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={LAT}&lon={LON}&altitude={ALTITUDE}'
CACHE_KEY = 'weather:almaty:v1'
TIMEOUT = 6                  # секунд ждём ответ MET
MIN_FRESH = 10 * 60          # спрашиваем не чаще раза в 10 минут, даже если Expires раньше
MAX_FRESH = 2 * 3600
RETRY_AFTER = 5 * 60         # после ошибки — следующая попытка не раньше чем через 5 минут
KEEP = 24 * 3600             # сколько хранить прогноз
HOURS = 24                   # сколько часов прогноза хранить


class WeatherError(Exception):
    pass


# ---------------- Что за погода: код MET → картинка и название ----------------

# В легенде MET две опечатки, и API отдаёт их именно так
TYPOS = {'lightssleetshowersandthunder': 'lightsleetshowersandthunder',
         'lightssnowshowersandthunder': 'lightsnowshowersandthunder'}
# Облачность для фона: 0 — ясно, 1 — пара облаков, 2 — переменная, 3 — почти сплошная, 4 — пасмурно
SKY = {'clearsky': (0, 'Ясно'), 'fair': (1, 'Малооблачно'), 'partlycloudy': (2, 'Переменная облачность'),
       'cloudy': (4, 'Пасмурно'), 'fog': (4, 'Туман')}
# Осадки: (небольшой, обычный, сильный)
PRECIP_LABELS = {
    'rain': ('Небольшой дождь', 'Дождь', 'Сильный дождь'),
    'rainshowers': ('Кратковременный дождь', 'Ливень', 'Сильный ливень'),
    'sleet': ('Небольшой мокрый снег', 'Мокрый снег', 'Сильный мокрый снег'),
    'sleetshowers': ('Небольшой мокрый снег', 'Мокрый снег', 'Сильный мокрый снег'),
    'snow': ('Небольшой снег', 'Снег', 'Сильный снег'),
    'snowshowers': ('Небольшой снег', 'Снегопад', 'Сильный снегопад'),
}
THUNDER_LABELS = {'rain': ('Небольшая гроза', 'Гроза', 'Сильная гроза'),
                  'sleet': ('Гроза, мокрый снег',) * 3, 'snow': ('Гроза, снег',) * 3}

# Короткие имена для просмотра: /dashboard/?weather=storm
DEMO_ALIASES = {
    'sun': 'clearsky', 'clear': 'clearsky', 'clouds': 'partlycloudy', 'drizzle': 'lightrain',
    'showers': 'rainshowers', 'storm': 'heavyrainandthunder', 'thunder': 'rainandthunder',
}
DEMO_TIMES = {
    'day': {'sun': (38, 185, True)},
    'morning': {'sun': (2, 110, True)},
    'evening': {'sun': (2, 252, False)},
    'dawn': {'sun': (-4, 100, True)},
    'dusk': {'sun': (-4, 262, False)},
    'night': {'sun': (-35, 340, False), 'moon': (32, 165, 0.62, True)},
}


def describe(symbol):
    """Код MET ('lightrainshowers_day') → что рисовать и как назвать; непонятный код — None"""
    code = (symbol or '').split('_')[0].strip()
    code = TYPOS.get(code, code)
    if code in SKY:
        clouds, label = SKY[code]
        return {'code': code, 'clouds': clouds, 'precip': None, 'intensity': 0, 'showers': False,
                'thunder': False, 'fog': code == 'fog', 'label': label}
    rest = code
    thunder = rest.endswith('andthunder')
    if thunder:
        rest = rest[:-len('andthunder')]
    showers = rest.endswith('showers')
    if showers:
        rest = rest[:-len('showers')]
    intensity = 2
    for prefix, level in (('light', 1), ('heavy', 3)):
        if rest.startswith(prefix):
            rest, intensity = rest[len(prefix):], level
    if rest not in ('rain', 'sleet', 'snow'):
        return None
    if thunder:
        label = THUNDER_LABELS[rest][intensity - 1]
    else:
        label = PRECIP_LABELS[rest + ('showers' if showers else '')][intensity - 1]
    # Ливни — облака с просветами (бывает и солнце), обложной дождь — сплошные тучи
    clouds = 4 if not showers else 3 if thunder or intensity == 3 else 2
    return {'code': code, 'clouds': clouds, 'precip': rest, 'intensity': intensity, 'showers': showers,
            'thunder': thunder, 'fog': False, 'label': label}


def symbol_from_clouds(cloud):
    """Код MET почему-то не пришёл — облачность в процентах"""
    if cloud is None:
        return None
    return 'clearsky' if cloud < 12 else 'fair' if cloud < 35 else 'partlycloudy' if cloud < 75 else 'cloudy'


def icon_for(info, night):
    """Значок Font Awesome для подписи погоды"""
    if info['thunder']:
        return 'fa-cloud-bolt'
    if info['precip'] == 'snow':
        return 'fa-snowflake'
    if info['precip']:
        if info['intensity'] == 3:
            return 'fa-cloud-showers-heavy'
        if info['showers']:
            return 'fa-cloud-moon-rain' if night else 'fa-cloud-sun-rain'
        return 'fa-cloud-rain'
    if info['fog']:
        return 'fa-smog'
    if info['clouds'] >= 3:
        return 'fa-cloud'
    if info['clouds'] == 2:
        return 'fa-cloud-moon' if night else 'fa-cloud-sun'
    return 'fa-moon' if night else 'fa-sun'


def feels_like(temp, humidity, wind):
    """Как ощущается (формула Стедмана, как у метеобюро Австралии): влажность греет, ветер холодит"""
    if temp is None or humidity is None or wind is None:
        return None
    vapour = humidity / 100 * 6.105 * math.exp(17.27 * temp / (237.7 + temp))
    return temp + 0.33 * vapour - 0.70 * wind - 4.00


# ---------------- Прогноз MET: запрос, разбор, кэш ----------------

def user_agent(request):
    # MET просит назвать приложение и где найти владельца
    return f'EnjoyEnglishCRM/1.0 (+https://{request.get_host()})'


def download(agent, last_modified=None):
    """Ответ MET: (данные или None, если с прошлого раза не изменились; Expires; Last-Modified)"""
    headers = {'User-Agent': agent, 'Accept': 'application/json', 'Accept-Encoding': 'gzip'}
    if last_modified:
        headers['If-Modified-Since'] = last_modified
    request = urllib.request.Request(API_URL, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read()
            if response.headers.get('Content-Encoding') == 'gzip':
                body = gzip.decompress(body)
            return json.loads(body.decode('utf-8')), response.headers.get('Expires'), response.headers.get('Last-Modified')
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, e.headers.get('Expires'), last_modified
        raise WeatherError(f'MET ответил {e.code}') from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise WeatherError(str(e)) from e


def parse_series(payload):
    """Прогноз по часам: [{'t': начало часа (unix), 'temp', 'wind', 'wind_dir', 'humidity', 'cloud', 'symbol', 'precip'}]"""
    series = []
    for item in ((payload or {}).get('properties') or {}).get('timeseries') or []:
        try:
            start = datetime.fromisoformat(item['time'].replace('Z', '+00:00')).timestamp()
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        data = item.get('data') or {}
        details = (data.get('instant') or {}).get('details') or {}
        hour = data.get('next_1_hours') or {}
        six = data.get('next_6_hours') or {}
        series.append({
            't': int(start),
            'temp': details.get('air_temperature'),
            'wind': details.get('wind_speed'),
            'wind_dir': details.get('wind_from_direction'),
            'humidity': details.get('relative_humidity'),
            'cloud': details.get('cloud_area_fraction'),
            'symbol': (hour.get('summary') or {}).get('symbol_code') or (six.get('summary') or {}).get('symbol_code'),
            'precip': (hour.get('details') or {}).get('precipitation_amount'),
        })
    series.sort(key=lambda e: e['t'])
    return series[:HOURS]


def _expires_at(header, now):
    try:
        when = parsedate_to_datetime(header).timestamp()
    except (TypeError, ValueError, IndexError):
        when = now + 30 * 60
    return min(max(when, now + MIN_FRESH), now + MAX_FRESH)


def forecast(agent, fetch=True, now=None):
    """Прогноз из кэша: (часы, когда получен). Устарел и fetch=True — спросить MET"""
    now = now or clock.time()
    stored = cache.get(CACHE_KEY) or {}
    if not fetch or now < stored.get('expires', 0):
        return stored.get('series') or [], stored.get('fetched')
    lock = CACHE_KEY + ':lock'
    if not cache.add(lock, 1, TIMEOUT + 5):
        return stored.get('series') or [], stored.get('fetched')   # уже спрашивает другой процесс сервера
    try:
        payload, expires, last_modified = download(agent, stored.get('last_modified') if stored.get('series') else None)
        if payload is None:   # 304: не изменилось
            stored = dict(stored, expires=_expires_at(expires, now))
        else:
            series = parse_series(payload)
            if not series:
                raise WeatherError('пустой прогноз')
            stored = {'series': series, 'fetched': now, 'expires': _expires_at(expires, now), 'last_modified': last_modified}
    except WeatherError as e:
        log.warning('Погода: не удалось получить прогноз MET Norway — %s', e)
        stored = dict(stored, expires=now + RETRY_AFTER)
    finally:
        cache.delete(lock)
    cache.set(CACHE_KEY, stored, KEEP)
    return stored.get('series') or [], stored.get('fetched')


def current_entry(series, now):
    """Час прогноза, в котором сейчас; прогноз старше 3 часов не годится"""
    past = [e for e in series if e['t'] <= now]
    if past:
        return past[-1] if now - past[-1]['t'] < 3 * 3600 else None
    return series[0] if series and series[0]['t'] - now < 3600 else None


# ---------------- Солнце и луна над Алматы ----------------

def _days(ts):
    """unix-время → дни от эпохи J2000.0"""
    return ts / 86400 + 2440587.5 - 2451545.0


def _horizontal(ra, dec, d):
    """Прямое восхождение и склонение (градусы) → высота над горизонтом и азимут (от севера по часовой)"""
    hour_angle = math.radians((280.46061837 + 360.98564736629 * d + LON - ra) % 360)
    lat, dec = math.radians(LAT), math.radians(dec)
    alt = math.asin(math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(hour_angle))
    az = math.atan2(-math.sin(hour_angle) * math.cos(dec),
                    math.cos(lat) * math.sin(dec) - math.sin(lat) * math.cos(dec) * math.cos(hour_angle))
    return math.degrees(alt), math.degrees(az) % 360


def _equatorial(lon, lat, d):
    """Эклиптические координаты → прямое восхождение и склонение"""
    eps = math.radians(23.439 - 0.0000004 * d)
    lon, lat = math.radians(lon), math.radians(lat)
    ra = math.atan2(math.sin(lon) * math.cos(eps) - math.tan(lat) * math.sin(eps), math.cos(lon))
    dec = math.asin(math.sin(lat) * math.cos(eps) + math.cos(lat) * math.sin(eps) * math.sin(lon))
    return math.degrees(ra) % 360, math.degrees(dec)


def _sun_longitude(d):
    g = math.radians((357.528 + 0.9856003 * d) % 360)
    return (280.460 + 0.9856474 * d + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360


def sun_position(ts):
    """Солнце: (высота, азимут) в градусах"""
    d = _days(ts)
    return _horizontal(*_equatorial(_sun_longitude(d), 0, d), d)


def _moon_ecliptic(d):
    """Долгота, широта Луны (градусы) и расстояние (в радиусах Земли) — главные поправки, точность около градуса"""
    ds = d + 1.5   # дни от 0 января 2000 года
    node = 125.1228 - 0.0529538083 * ds
    incl, ecc, axis = 5.1454, 0.054900, 60.2666
    perigee = 318.0634 + 0.1643573223 * ds
    mean = (115.3654 + 13.0649929509 * ds) % 360
    ecc_anomaly = mean + math.degrees(ecc * math.sin(math.radians(mean)) * (1 + ecc * math.cos(math.radians(mean))))
    for _ in range(5):   # уравнение Кеплера
        ecc_anomaly -= (ecc_anomaly - math.degrees(ecc * math.sin(math.radians(ecc_anomaly))) - mean) \
            / (1 - ecc * math.cos(math.radians(ecc_anomaly)))
    e_rad = math.radians(ecc_anomaly)
    xv, yv = axis * (math.cos(e_rad) - ecc), axis * math.sqrt(1 - ecc * ecc) * math.sin(e_rad)
    dist = math.hypot(xv, yv)
    vw = math.atan2(yv, xv) + math.radians(perigee)
    n_rad, i_rad = math.radians(node), math.radians(incl)
    xh = dist * (math.cos(n_rad) * math.cos(vw) - math.sin(n_rad) * math.sin(vw) * math.cos(i_rad))
    yh = dist * (math.sin(n_rad) * math.cos(vw) + math.cos(n_rad) * math.sin(vw) * math.cos(i_rad))
    zh = dist * math.sin(vw) * math.sin(i_rad)
    lon, lat = math.degrees(math.atan2(yh, xh)), math.degrees(math.atan2(zh, math.hypot(xh, yh)))
    # Возмущения от Солнца
    sun_mean = 356.0470 + 0.9856002585 * ds
    moon_long = node + perigee + mean
    elong = moon_long - (sun_mean + 282.9404 + 4.70935e-5 * ds)
    arg = moon_long - node

    def s(x):
        return math.sin(math.radians(x))
    lon += (-1.274 * s(mean - 2 * elong) + 0.658 * s(2 * elong) - 0.186 * s(sun_mean) - 0.059 * s(2 * mean - 2 * elong)
            - 0.057 * s(mean - 2 * elong + sun_mean) + 0.053 * s(mean + 2 * elong) + 0.046 * s(2 * elong - sun_mean)
            + 0.041 * s(mean - sun_mean) - 0.035 * s(elong) - 0.031 * s(mean + sun_mean)
            - 0.015 * s(2 * arg - 2 * elong) + 0.011 * s(mean - 4 * elong))
    lat += (-0.173 * s(arg - 2 * elong) - 0.055 * s(mean - arg - 2 * elong) - 0.046 * s(mean + arg - 2 * elong)
            + 0.033 * s(arg + 2 * elong) + 0.017 * s(2 * mean + arg))
    dist += -0.58 * math.cos(math.radians(mean - 2 * elong)) - 0.46 * math.cos(math.radians(2 * elong))
    return lon % 360, lat, dist


def moon_position(ts):
    """Луна: (высота, азимут, освещённая доля 0…1, растёт ли)"""
    d = _days(ts)
    lon, lat, dist = _moon_ecliptic(d)
    alt, az = _horizontal(*_equatorial(lon, lat, d), d)
    alt -= math.degrees(math.asin(1 / dist)) * math.cos(math.radians(alt))   # Луна близко: с поверхности Земли она ниже
    sun_lon = _sun_longitude(d)
    elong = math.acos(max(-1, min(1, math.cos(math.radians(lon - sun_lon)) * math.cos(math.radians(lat)))))
    return alt, az, (1 - math.cos(elong)) / 2, (lon - sun_lon) % 360 < 180


def sun_times(day):
    """Восход и закат (unix-время) в этот день по часовому поясу сайта; полярных дней в Алматы нет"""
    start = datetime.combine(day, time.min, tzinfo=timezone.get_current_timezone()).timestamp()

    def height(ts):
        return sun_position(ts)[0] + 0.833   # край диска с учётом преломления у горизонта
    found = {}
    step = 600
    for i in range(24 * 3600 // step):
        a, b = start + i * step, start + (i + 1) * step
        ha, hb = height(a), height(b)
        if (ha < 0) != (hb < 0):
            for _ in range(20):
                mid = (a + b) / 2
                if (height(mid) < 0) == (ha < 0):
                    a = mid
                else:
                    b = mid
            found['sunrise' if ha < 0 else 'sunset'] = (a + b) / 2
    return found.get('sunrise'), found.get('sunset')


def _hhmm(ts):
    return timezone.localtime(datetime.fromtimestamp(ts, tz=dt_timezone.utc)).strftime('%H:%M') if ts else ''


# ---------------- Всё вместе ----------------

def snapshot(agent='', fetch=True, demo=None, demo_time=None, now=None):
    """Погода сейчас и что нарисовать на фоне — для страницы и для /api/weather/"""
    now = now or clock.time()
    sun_alt, sun_az = sun_position(now)
    rising = sun_position(now + 600)[0] > sun_alt
    moon_alt, moon_az, moon_lit, waxing = moon_position(now)

    entry, fetched, info = None, None, None
    demo_code = DEMO_ALIASES.get(demo, demo) if demo else None
    if demo_code and describe(demo_code):
        info = describe(demo_code)
        cold = info['precip'] in ('snow', 'sleet')
        entry = {'temp': -4 if cold else 14, 'wind': 6 if info['thunder'] or info['intensity'] == 3 else 2,
                 'wind_dir': 250, 'humidity': 85 if info['precip'] or info['fog'] else 45}
        if demo_time in DEMO_TIMES:
            sun_alt, sun_az, rising = DEMO_TIMES[demo_time]['sun']
            moon_alt, moon_az, moon_lit, waxing = DEMO_TIMES[demo_time].get('moon', (-20, 0, 0.5, True))
    else:
        demo_code = None
        series, fetched = forecast(agent, fetch=fetch, now=now)
        entry = current_entry(series, now)
        if entry:
            info = describe(entry['symbol']) or describe(symbol_from_clouds(entry.get('cloud')))

    night = sun_alt < -0.833
    temp = entry.get('temp') if entry else None
    feels = feels_like(temp, entry.get('humidity'), entry.get('wind')) if entry else None
    sunrise, sunset = sun_times(timezone.localtime(datetime.fromtimestamp(now, tz=dt_timezone.utc)).date())
    stored = cache.get(CACHE_KEY) or {}
    return {
        'ok': bool(info),
        'demo': bool(demo_code),
        'stale': not demo_code and now >= stored.get('expires', 0),
        'city': CITY,
        'label': info['label'] if info else '',
        'icon': icon_for(info, night) if info else '',
        'temp': round(temp) if temp is not None else None,
        'feels': round(feels) if feels is not None and temp is not None and abs(feels - temp) >= 2 else None,
        'wind': round(entry['wind']) if entry and entry.get('wind') is not None else None,
        'humidity': round(entry['humidity']) if entry and entry.get('humidity') is not None else None,
        'sunrise': _hhmm(sunrise),
        'sunset': _hhmm(sunset),
        'updated': _hhmm(fetched) if fetched else '',
        'scene': {
            'clouds': info['clouds'] if info else 1,
            'precip': info['precip'] if info else None,
            'intensity': info['intensity'] if info else 0,
            'showers': info['showers'] if info else False,
            'thunder': info['thunder'] if info else False,
            'fog': info['fog'] if info else False,
            'wind': (entry.get('wind') or 0) if entry else 0,
            'wind_dir': (entry.get('wind_dir') or 0) if entry else 0,
            'sun': {'alt': round(sun_alt, 2), 'az': round(sun_az, 1), 'rising': rising},
            'moon': {'alt': round(moon_alt, 2), 'az': round(moon_az, 1), 'lit': round(moon_lit, 3), 'waxing': waxing},
        },
    }


@crm_view
def weather_api(request):
    data = snapshot(user_agent(request), demo=request.GET.get('demo') or None, demo_time=request.GET.get('time'))
    response = JsonResponse(data)
    response['Cache-Control'] = 'private, max-age=60'
    return response
