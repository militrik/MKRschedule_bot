from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def simple_list_kb(options: list[tuple[str, str]], cols=2) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(options), cols):
        chunk = options[i:i+cols]
        rows.append([InlineKeyboardButton(text=t, callback_data=cb) for cb, t in chunk])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def paginated_kb(options: list[tuple[str, str]], page: int, per_page: int, prefix: str) -> InlineKeyboardMarkup:
    start = page * per_page
    chunk = options[start:start+per_page]
    rows = [[InlineKeyboardButton(text=t, callback_data=f"{prefix}:{cb}")] for cb, t in chunk]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"{prefix}:__prev__"))
    if start + per_page < len(options):
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"{prefix}:__next__"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)
