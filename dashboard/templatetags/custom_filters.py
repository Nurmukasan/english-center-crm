from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Получает значение из словаря по ключу"""
    return dictionary.get(key, [])

@register.filter
def phone_digits(value):
    """«8 (700) 123-45-67» → «77001234567» для ссылок tel: и wa.me; не номер — пусто"""
    from dashboard.whatsapp import normalize_phone
    return normalize_phone(value) or ''
