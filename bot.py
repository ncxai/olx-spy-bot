import os
import sys
import html
import re
import time
import asyncio
import logging
import contextlib
from typing import Optional, Tuple, Dict, Any, List

# Ensure UTF-8 output on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

import database
from parser import parse_olx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("olx_spy_bot")

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
DEFAULT_INTERVAL = int(os.getenv("DEFAULT_INTERVAL", "10"))

dp = Dispatcher()

# Supported locations dictionary
LOCATIONS = {
    "list": "Весь Узбекистан 🇺🇿",
    "tashkent": "Ташкент 🏙",
    "tashkentskaya-oblast": "Ташкентская обл. 🌄",
    "samarkand": "Самарканд 🕌",
    "buhara": "Бухара 🏺",
    "andizhan": "Андижан 🏘",
    "fergana": "Фергана 🌴",
    "namangan": "Наманган 🌸",
    "karshi": "Карши 🏞",
    "navoi": "Навои 🏭",
    "urgench": "Ургенч 🏛",
    "nukus": "Нукус 🏜",
    "termez": "Термез ☀️",
    "dzhizak": "Джизак 🌲",
    "gulistan": "Гулистан 🌾",
}

LOCATION_ALIASES = {
    "весь узбекистан": "list", "все": "list", "всё": "list", "узбекистан": "list", "all": "list", "list": "list",
    "ташкент": "tashkent", "tashkent": "tashkent", "тошкент": "tashkent",
    "ташкентская область": "tashkentskaya-oblast", "таш область": "tashkentskaya-oblast", "ташобласть": "tashkentskaya-oblast",
    "самарканд": "samarkand", "samarkand": "samarkand", "самаркандская область": "samarkand",
    "бухара": "buhara", "buhara": "buhara", "bukhara": "buhara", "бухарская область": "buhara",
    "андижан": "andizhan", "andizhan": "andizhan", "андижанская область": "andizhan",
    "фергана": "fergana", "fergana": "fergana", "ферганская область": "fergana",
    "наманган": "namangan", "namangan": "namangan", "наманганская область": "namangan",
    "карши": "karshi", "karshi": "karshi", "кашкадарья": "karshi", "кашкадарьинская область": "karshi",
    "навои": "navoi", "navoi": "navoi", "навоийская область": "navoi",
    "ургенч": "urgench", "urgench": "urgench", "хорезм": "urgench", "хорезмская область": "urgench",
    "нукус": "nukus", "nukus": "nukus", "каракалпакстан": "nukus",
    "термез": "termez", "termez": "termez", "сурхандарья": "termez", "сурхандарьинская область": "termez",
    "джизак": "dzhizak", "dzhizak": "dzhizak", "джизакская область": "dzhizak",
    "гулистан": "gulistan", "gulistan": "gulistan", "сырдарья": "gulistan", "сырдарьинская область": "gulistan",
}


def get_location_name(slug: Optional[str]) -> str:
    """Returns friendly human-readable name for location slug."""
    if not slug or slug not in LOCATIONS:
        return LOCATIONS["list"]
    return LOCATIONS[slug]


def format_price_range(min_p: Optional[int], max_p: Optional[int]) -> str:
    """Formats price range for friendly user display."""
    if min_p and max_p:
        return f"от {min_p:,} до {max_p:,} сум".replace(",", " ")
    elif min_p:
        return f"от {min_p:,} сум".replace(",", " ")
    elif max_p:
        return f"до {max_p:,} сум".replace(",", " ")
    else:
        return "любая цена"


def format_seller_name(stype: Optional[str]) -> str:
    if stype == "private":
        return "Частные лица 👤"
    elif stype == "business":
        return "Бизнес / Магазины 🏢"
    return "Все продавцы 🌐"


def format_sort_name(sort_by: Optional[str]) -> str:
    if sort_by == "filter_float_price:asc":
        return "Сначала дешевые 💵"
    return "Сначала новые (по дате) 🕒"


# --- Inline Keyboards ---

def get_interval_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="⚡ 2 мин", callback_data="set_int_2"),
            InlineKeyboardButton(text="⚡ 5 мин", callback_data="set_int_5"),
            InlineKeyboardButton(text="⏱ 10 мин", callback_data="set_int_10"),
        ],
        [
            InlineKeyboardButton(text="⏱ 15 мин", callback_data="set_int_15"),
            InlineKeyboardButton(text="🕐 30 мин", callback_data="set_int_30"),
            InlineKeyboardButton(text="🕐 60 мин", callback_data="set_int_60"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_price_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="💵 До 1 млн", callback_data="set_price_0_1m"),
            InlineKeyboardButton(text="💵 1 – 3 млн", callback_data="set_price_1m_3m"),
        ],
        [
            InlineKeyboardButton(text="💵 3 – 7 млн", callback_data="set_price_3m_7m"),
            InlineKeyboardButton(text="💵 7 – 15 млн", callback_data="set_price_7m_15m"),
        ],
        [
            InlineKeyboardButton(text="💵 От 15 млн", callback_data="set_price_15m_up"),
            InlineKeyboardButton(text="❌ Сбросить", callback_data="set_price_reset"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_location_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🇺🇿 Весь Узбекистан", callback_data="set_loc_list")],
        [
            InlineKeyboardButton(text="🏙 Ташкент", callback_data="set_loc_tashkent"),
            InlineKeyboardButton(text="🌄 Таш. область", callback_data="set_loc_tashkentskaya-oblast"),
        ],
        [
            InlineKeyboardButton(text="🕌 Самарканд", callback_data="set_loc_samarkand"),
            InlineKeyboardButton(text="🏺 Бухара", callback_data="set_loc_buhara"),
        ],
        [
            InlineKeyboardButton(text="🏘 Андижан", callback_data="set_loc_andizhan"),
            InlineKeyboardButton(text="🌴 Фергана", callback_data="set_loc_fergana"),
        ],
        [
            InlineKeyboardButton(text="🌸 Наманган", callback_data="set_loc_namangan"),
            InlineKeyboardButton(text="🏞 Карши", callback_data="set_loc_karshi"),
        ],
        [
            InlineKeyboardButton(text="🏭 Навои", callback_data="set_loc_navoi"),
            InlineKeyboardButton(text="🏛 Ургенч", callback_data="set_loc_urgench"),
        ],
        [
            InlineKeyboardButton(text="🏜 Нукус", callback_data="set_loc_nukus"),
            InlineKeyboardButton(text="☀️ Термез", callback_data="set_loc_termez"),
        ],
        [
            InlineKeyboardButton(text="🌲 Джизак", callback_data="set_loc_dzhizak"),
            InlineKeyboardButton(text="🌾 Гулистан", callback_data="set_loc_gulistan"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_seller_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="👤 Только частные", callback_data="set_seller_private"),
            InlineKeyboardButton(text="🏢 Только бизнес", callback_data="set_seller_business"),
        ],
        [
            InlineKeyboardButton(text="🌐 Все продавцы", callback_data="set_seller_all"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_sort_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="🕒 Сначала новые (по дате)", callback_data="set_sort_date"),
        ],
        [
            InlineKeyboardButton(text="💵 Сначала дешевые (по цене)", callback_data="set_sort_price"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def parse_price_args(arg_text: str) -> Tuple[Optional[int], Optional[int]]:
    cleaned = arg_text.strip().lower()
    if cleaned in ("reset", "сброс", "отмена", "clear", "0"):
        return None, None

    if cleaned.startswith("от"):
        nums = re.findall(r"\d+", cleaned.replace(" ", ""))
        return (int(nums[0]), None) if nums else (None, None)
    if cleaned.startswith("до"):
        nums = re.findall(r"\d+", cleaned.replace(" ", ""))
        return (None, int(nums[0])) if nums else (None, None)

    clean_spaces = cleaned.replace("сум", "").replace("uzs", "").replace("$", "")
    parts = re.split(r"[-–—\s]+", clean_spaces)
    nums = [int(p) for p in parts if p.isdigit()]

    if len(nums) == 1:
        return nums[0], None
    elif len(nums) >= 2:
        min_val = nums[0] if nums[0] > 0 else None
        max_val = nums[1] if nums[1] > 0 else None
        if min_val and max_val and min_val > max_val:
            min_val, max_val = max_val, min_val
        return min_val, max_val

    return None, None


# --- Ad Card Sender Helper ---

async def send_ad_card(
    bot: Bot,
    chat_id: int,
    ad: Dict[str, Any],
    search_query: Optional[str] = None,
    location_name: Optional[str] = None
):
    """
    Sends rich ad card with photo (if available), smart deal badge,
    and action buttons [🔗 Открыть на OLX] and [⭐ В избранное].
    """
    hash_id = database.cache_ad(
        title=ad["title"],
        price=ad["price"],
        link=ad["link"],
        photo=ad.get("photo")
    )

    deal_badge = ""
    if ad.get("is_deal"):
        discount = ad.get("deal_discount", 20)
        deal_badge = f"🔥 <b>ВЫГОДНАЯ СДЕЛКА! На ~{discount}% ниже средней цены!</b>\n\n"

    query_info = f"🔍 <i>Поиск: {html.escape(search_query)}</i>\n" if search_query else ""
    loc_info = f"📍 <i>Локация: {html.escape(location_name)}</i>\n" if location_name else ""

    text = (
        f"{deal_badge}"
        f"{query_info}"
        f"{loc_info}"
        f"📌 <b>{html.escape(ad['title'])}</b>\n"
        f"💰 <b>Цена:</b> {html.escape(ad['price'])}\n\n"
        f"🔗 <a href='{ad['link']}'>Перейти к объявлению</a>"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔗 Открыть на OLX", url=ad["link"]),
                InlineKeyboardButton(text="⭐ В избранное", callback_data=f"fav_add:{hash_id}"),
            ]
        ]
    )

    photo_url = ad.get("photo")
    sent = False
    if photo_url:
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=photo_url,
                caption=text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
            sent = True
        except Exception as e:
            logger.warning("Failed to send photo for ad: %s. Falling back to text message.", e)

    if not sent:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=False
        )


# --- Bot Handlers ---

@dp.message(CommandStart())
async def handle_start(message: Message, command: CommandObject):
    chat_id = message.chat.id
    query_arg = (command.args or "").strip()

    if query_arg:
        await process_new_search_query(message, query_arg)
        return

    tasks = database.get_user_tasks(chat_id)
    tasks_count = len(tasks)

    welcome_text = (
        "👋 <b>Добро пожаловать в OLX Spy Bot!</b>\n\n"
        "Умный мониторинг объявлений на <b>olx.uz</b> 24/7 с фото, детекцией выгодных цен и гибкими фильтрами.\n\n"
        "🔹 <b>Как начать:</b>\n"
        "Просто отправьте текст товара (например: <code>iPhone 13 Pro</code> или <code>MacBook Air</code>).\n\n"
        "🔹 <b>Команды управления:</b>\n"
        "• <b>/tasks</b> — Список ваших активных поисков\n"
        "• <b>/favorites</b> — Избранные объявления ⭐\n"
        "• <b>/city</b> — Выбрать город или регион\n"
        "• <b>/price</b> — Фильтр цен (кнопки или от/до)\n"
        "• <b>/seller</b> — Только частники или магазины\n"
        "• <b>/stop_words</b> — Исключить минус-слова\n"
        "• <b>/sort</b> — Сортировка (новые или дешевые)\n"
        "• <b>/interval</b> — Частота проверки (2, 5, 10 мин...)\n"
        "• <b>/status</b> — Статус текущего поиска\n"
        "• <b>/stop</b> — Остановить отслеживание\n\n"
        f"📊 <i>Активных поисков сейчас: <b>{tasks_count}</b></i>"
    )
    await message.answer(welcome_text)


@dp.message(Command("tasks", "my_tasks", "list"))
async def handle_tasks_command(message: Message):
    """Displays all active monitoring tasks of the user with management buttons."""
    chat_id = message.chat.id
    tasks = database.get_user_tasks(chat_id)

    if not tasks:
        await message.answer(
            "📋 <b>У вас пока нет активных поисков.</b>\n\n"
            "Отправьте мне название любого товара (например: <code>iPhone 13 Pro</code>), "
            "чтобы добавить его на отслеживание."
        )
        return

    text = f"📋 <b>Ваши активные поиски ({len(tasks)}):</b>\n\n"
    buttons = []

    for idx, t in enumerate(tasks, 1):
        price_str = format_price_range(t.get("min_price"), t.get("max_price"))
        loc_str = get_location_name(t.get("location"))
        stops = t.get("stop_words")
        stops_str = f" | 🚫 Минус: {stops}" if stops else ""
        seller_str = " | 👤 Частные" if t.get("seller_type") == "private" else ""

        text += (
            f"<b>{idx}. {html.escape(t['query'])}</b>\n"
            f"   ⏱ {t['interval_min']} мин | 📍 {loc_str} | 💰 {price_str}{seller_str}{stops_str}\n\n"
        )
        buttons.append([
            InlineKeyboardButton(text=f"❌ Удалить «{t['query'][:18]}»", callback_data=f"del_task:{t['id']}")
        ])

    buttons.append([
        InlineKeyboardButton(text="🗑 Удалить все поиски", callback_data="del_all_tasks")
    ])

    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.message(Command("favorites", "fav"))
async def handle_favorites_command(message: Message):
    """Displays user saved favorite ads."""
    chat_id = message.chat.id
    favs = database.get_favorites(chat_id)

    if not favs:
        await message.answer(
            "⭐ <b>Ваше избранное пусто.</b>\n\n"
            "Нажимайте кнопку <b>«⭐ В избранное»</b> под интересными объявлениями, "
            "чтобы сохранять их сюда."
        )
        return

    await message.answer(f"⭐ <b>Ваши сохраненные объявления ({len(favs)}):</b>")

    for f in favs[:10]:
        fav_buttons = [
            [
                InlineKeyboardButton(text="🔗 Открыть на OLX", url=f["link"]),
                InlineKeyboardButton(text="🗑 Удалить из избранного", callback_data=f"fav_del:{f['id']}"),
            ]
        ]
        text = (
            f"📌 <b>{html.escape(f['title'])}</b>\n"
            f"💰 <b>Цена:</b> {html.escape(f['price'])}\n"
            f"🔗 <a href='{f['link']}'>Ссылка на товар</a>"
        )
        if f.get("photo"):
            try:
                await message.answer_photo(
                    photo=f["photo"],
                    caption=text,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=fav_buttons)
                )
                continue
            except Exception:
                pass

        await message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=fav_buttons),
            disable_web_page_preview=False
        )


@dp.message(Command("stop_words", "minus", "stopwords"))
async def handle_stop_words_command(message: Message, command: CommandObject):
    """Configures stop-words to exclude unwanted ads."""
    chat_id = message.chat.id
    arg = (command.args or "").strip()

    if arg:
        if arg.lower() in ("reset", "сброс", "0", "отмена", "clear"):
            database.set_stop_words(chat_id, "")
            await message.answer("✅ <b>Минус-слова очищены!</b> Теперь объявления не будут фильтроваться по стоп-словам.")
        else:
            database.set_stop_words(chat_id, arg)
            await message.answer(
                f"✅ <b>Минус-слова установлены:</b> <code>{html.escape(arg)}</code>\n\n"
                f"Объявления, содержащие эти слова в названии, будут автоматически отсекаться."
            )
        return

    latest = database.get_latest_task(chat_id)
    cur_stops = latest.get("stop_words") if latest else ""
    cur_str = cur_stops if cur_stops else "не заданы"

    buttons = [
        [
            InlineKeyboardButton(text="➕ чехол", callback_data="add_stop_чехол"),
            InlineKeyboardButton(text="➕ копия", callback_data="add_stop_копия"),
            InlineKeyboardButton(text="➕ запчасти", callback_data="add_stop_запчасти"),
        ],
        [
            InlineKeyboardButton(text="➕ обмен", callback_data="add_stop_обмен"),
            InlineKeyboardButton(text="➕ реплика", callback_data="add_stop_реплика"),
            InlineKeyboardButton(text="❌ Сбросить", callback_data="reset_stops"),
        ]
    ]

    await message.answer(
        f"🚫 <b>Фильтр минус-слов (стоп-слов)</b>\n\n"
        f"Текущие минус-слова: <b>{html.escape(cur_str)}</b>\n\n"
        f"Вы можете добавить популярные слова кнопками ниже либо написать свои через запятую:\n"
        f"<code>/stop_words чехол, копия, на запчасти, icloud</code>\n\n"
        f"Для сброса: <code>/stop_words сброс</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@dp.message(Command("seller"))
async def handle_seller_command(message: Message, command: CommandObject):
    """Sets seller filter: private, business, or all."""
    chat_id = message.chat.id
    arg = (command.args or "").strip().lower()

    if arg in ("private", "частник", "частные", "частное"):
        database.set_seller_type(chat_id, "private")
        await message.answer("✅ Фильтр установлен: <b>Только частные объявления 👤</b>")
    elif arg in ("business", "бизнес", "магазин", "магазины"):
        database.set_seller_type(chat_id, "business")
        await message.answer("✅ Фильтр установлен: <b>Только бизнес / магазины 🏢</b>")
    elif arg in ("all", "все", "любой"):
        database.set_seller_type(chat_id, "all")
        await message.answer("✅ Фильтр установлен: <b>Все продавцы 🌐</b>")
    else:
        await message.answer(
            "👤 <b>Фильтр продавцов на OLX</b>\n\n"
            "Выберите тип продавца:",
            reply_markup=get_seller_keyboard()
        )


@dp.message(Command("sort"))
async def handle_sort_command(message: Message, command: CommandObject):
    """Sets sorting order: newest or cheapest."""
    chat_id = message.chat.id
    arg = (command.args or "").strip().lower()

    if arg in ("price", "дешевые", "цена"):
        database.set_sort_by(chat_id, "filter_float_price:asc")
        await message.answer("✅ Сортировка: <b>Сначала дешевые 💵</b>")
    elif arg in ("date", "новые", "дата"):
        database.set_sort_by(chat_id, "created_at:desc")
        await message.answer("✅ Сортировка: <b>Сначала новые (по дате) 🕒</b>")
    else:
        await message.answer(
            "🕒 <b>Порядок сортировки объявлений</b>\n\n"
            "Выберите порядок:",
            reply_markup=get_sort_keyboard()
        )


@dp.message(Command("stop"))
async def handle_stop(message: Message):
    chat_id = message.chat.id
    deleted = database.delete_all_tasks(chat_id)

    if deleted:
        await message.answer(
            "🛑 <b>Мониторинг успешно остановлен!</b>\n\n"
            "Все ваши поисковые задачи удалены из базы. Чтобы запустить новый поиск, "
            "просто отправьте название товара."
        )
    else:
        await message.answer(
            "ℹ️ У вас не было активных задач для отслеживания.\n"
            "Отправьте мне название товара в чат, чтобы запустить мониторинг."
        )


@dp.message(Command("status"))
async def handle_status(message: Message):
    chat_id = message.chat.id
    tasks = database.get_user_tasks(chat_id)

    if not tasks:
        await message.answer(
            "ℹ️ У вас пока нет активных задач поиска.\n"
            "Отправьте название интересующего товара, чтобы начать."
        )
        return

    latest = tasks[-1]
    price_str = format_price_range(latest.get("min_price"), latest.get("max_price"))
    loc_str = get_location_name(latest.get("location"))
    seller_str = format_seller_name(latest.get("seller_type"))
    sort_str = format_sort_name(latest.get("sort_by"))
    stops = latest.get("stop_words") or "нет"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏱ Интервал", callback_data="open_interval_menu"),
                InlineKeyboardButton(text="💰 Цена", callback_data="open_price_menu"),
            ],
            [
                InlineKeyboardButton(text="📍 Локация", callback_data="open_location_menu"),
                InlineKeyboardButton(text="👤 Продавец", callback_data="open_seller_menu"),
            ],
            [
                InlineKeyboardButton(text="🚫 Минус-слова", callback_data="open_stops_menu"),
                InlineKeyboardButton(text="📋 Все поиски", callback_data="open_tasks_menu"),
            ]
        ]
    )

    await message.answer(
        f"📊 <b>Статус активного поиска:</b>\n\n"
        f"🔍 Запрос: <b>{html.escape(latest['query'])}</b> (всего поисков: {len(tasks)})\n"
        f"⏱ Интервал: <b>каждые {latest['interval_min']} мин.</b>\n"
        f"💰 Цена: <b>{price_str}</b>\n"
        f"📍 Город: <b>{loc_str}</b>\n"
        f"👤 Продавец: <b>{seller_str}</b>\n"
        f"🚫 Минус-слова: <b>{html.escape(stops)}</b>\n"
        f"🕒 Сортировка: <b>{sort_str}</b>\n\n"
        f"Для управления выберите действие ниже или используйте /tasks:",
        reply_markup=keyboard
    )


@dp.message(Command("city", "location", "region"))
async def handle_city_command(message: Message, command: CommandObject):
    chat_id = message.chat.id
    arg = (command.args or "").strip().lower()

    if arg:
        slug = LOCATION_ALIASES.get(arg)
        if not slug:
            for alias, target in LOCATION_ALIASES.items():
                if arg in alias:
                    slug = target
                    break

        if not slug:
            await message.answer(
                "⚠️ Не удалось распознать город. Выберите из списка:",
                reply_markup=get_location_keyboard()
            )
            return

        database.set_location(chat_id, slug)
        loc_name = get_location_name(slug)
        await message.answer(f"✅ <b>Местоположение обновлено:</b> <b>{loc_name}</b>")
        return

    latest = database.get_latest_task(chat_id)
    cur_loc = get_location_name(latest.get("location") if latest else None)

    await message.answer(
        f"📍 <b>Выбор местоположения для поиска</b>\n\n"
        f"Текущая локация: <b>{cur_loc}</b>\n\n"
        f"Выберите город или область ниже:",
        reply_markup=get_location_keyboard()
    )


@dp.message(Command("interval", "time"))
async def handle_interval_command(message: Message, command: CommandObject):
    chat_id = message.chat.id
    arg = (command.args or "").strip()

    if arg:
        try:
            minutes = int(arg)
            if minutes < 1 or minutes > 1440:
                await message.answer("⚠️ Пожалуйста, укажите интервал от 1 до 1440 минут (24 ч).")
                return

            database.update_interval(chat_id, minutes)
            await message.answer(f"✅ <b>Интервал проверки установлен:</b> каждые <b>{minutes} мин.</b>")
        except ValueError:
            await message.answer("⚠️ Неверный формат. Пример: <code>/interval 15</code>")
        return

    latest = database.get_latest_task(chat_id)
    cur_int = latest["interval_min"] if latest else DEFAULT_INTERVAL

    await message.answer(
        f"⏱ <b>Настройка интервала проверки</b>\n\n"
        f"Текущий интервал: <b>{cur_int} мин.</b>\n"
        f"Выберите периодичность кнопкой или отправьте <code>/interval &lt;число&gt;</code>:",
        reply_markup=get_interval_keyboard()
    )


@dp.message(Command("price", "filter"))
async def handle_price_command(message: Message, command: CommandObject):
    chat_id = message.chat.id
    arg = (command.args or "").strip()

    if arg:
        min_p, max_p = parse_price_args(arg)
        database.set_price_filter(chat_id, min_p, max_p)
        price_str = format_price_range(min_p, max_p)
        await message.answer(f"✅ <b>Фильтр цен обновлен:</b> <b>{price_str}</b>")
        return

    latest = database.get_latest_task(chat_id)
    cur_price_str = format_price_range(latest.get("min_price"), latest.get("max_price")) if latest else "не установлен"

    await message.answer(
        f"💰 <b>Фильтрация объявлений по цене</b>\n\n"
        f"Текущий фильтр: <b>{cur_price_str}</b>\n\n"
        f"Выберите диапазон кнопками ниже или отправьте точные значения:\n"
        f"• <code>/price 1000000 5000000</code> — от 1 до 5 млн\n"
        f"• <code>/price до 3000000</code> — до 3 млн сум\n"
        f"• <code>/price сброс</code> — отключить",
        reply_markup=get_price_keyboard()
    )


# --- Callback Queries ---

@dp.callback_query(F.data.startswith("fav_add:"))
async def callback_add_favorite(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    hash_id = callback.data.split(":")[-1]
    cached = database.get_cached_ad(hash_id)

    if not cached:
        await callback.answer("⚠️ Срок действия карточки истек", show_alert=True)
        return

    added = database.add_favorite(
        chat_id=chat_id,
        title=cached["title"],
        price=cached["price"],
        link=cached["link"],
        photo=cached.get("photo")
    )

    if added:
        await callback.answer("⭐ Добавлено в избранное! Посмотреть: /favorites", show_alert=False)
    else:
        await callback.answer("ℹ️ Это объявление уже есть в избранном!", show_alert=False)


@dp.callback_query(F.data.startswith("fav_del:"))
async def callback_del_favorite(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    fav_id = int(callback.data.split(":")[-1])
    database.delete_favorite(fav_id, chat_id)
    await callback.answer("Удалено из избранного 🗑")
    try:
        await callback.message.delete()
    except Exception:
        pass


@dp.callback_query(F.data.startswith("del_task:"))
async def callback_del_task(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    task_id = int(callback.data.split(":")[-1])
    deleted = database.delete_task_by_id(task_id, chat_id)
    if deleted:
        await callback.answer("Поиск удален ❌")
        # Refresh tasks view
        tasks = database.get_user_tasks(chat_id)
        if not tasks:
            await callback.message.edit_text("📋 Все поиски удалены. Чтобы начать новый поиск, отправьте название товара.")
        else:
            text = f"📋 <b>Ваши активные поиски ({len(tasks)}):</b>\n\n"
            buttons = []
            for idx, t in enumerate(tasks, 1):
                text += f"<b>{idx}. {html.escape(t['query'])}</b>\n   ⏱ {t['interval_min']} мин | 📍 {get_location_name(t.get('location'))}\n\n"
                buttons.append([InlineKeyboardButton(text=f"❌ Удалить «{t['query'][:18]}»", callback_data=f"del_task:{t['id']}")])
            buttons.append([InlineKeyboardButton(text="🗑 Удалить все", callback_data="del_all_tasks")])
            await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    else:
        await callback.answer("Задача уже была удалена", show_alert=True)


@dp.callback_query(F.data == "del_all_tasks")
async def callback_del_all_tasks(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    database.delete_all_tasks(chat_id)
    await callback.answer("Все поиски удалены 🗑")
    await callback.message.edit_text("📋 Все поиски успешно удалены. Чтобы начать, просто отправьте текст товара.")


@dp.callback_query(F.data == "open_interval_menu")
async def callback_open_interval(callback: CallbackQuery):
    await callback.message.answer("⏱ Выберите периодичность проверки:", reply_markup=get_interval_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "open_price_menu")
async def callback_open_price(callback: CallbackQuery):
    await callback.message.answer("💰 Выберите диапазон цен:", reply_markup=get_price_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "open_location_menu")
async def callback_open_loc(callback: CallbackQuery):
    await callback.message.answer("📍 Выберите регион или город поиска:", reply_markup=get_location_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "open_seller_menu")
async def callback_open_seller(callback: CallbackQuery):
    await callback.message.answer("👤 Выберите тип продавца:", reply_markup=get_seller_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "open_stops_menu")
async def callback_open_stops(callback: CallbackQuery):
    await handle_stop_words_command(callback.message, CommandObject(prefix="/", command="stop_words", args=None))
    await callback.answer()


@dp.callback_query(F.data == "open_tasks_menu")
async def callback_open_tasks(callback: CallbackQuery):
    await handle_tasks_command(callback.message)
    await callback.answer()


@dp.callback_query(F.data.startswith("set_loc_"))
async def callback_set_location(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    slug = callback.data.replace("set_loc_", "")
    database.set_location(chat_id, slug)
    loc_name = get_location_name(slug)
    await callback.answer(f"Локация: {loc_name} ✅")
    await callback.message.edit_text(f"📍 <b>Местоположение обновлено:</b> {loc_name}")


@dp.callback_query(F.data.startswith("set_int_"))
async def callback_set_interval(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    minutes = int(callback.data.split("_")[-1])
    database.update_interval(chat_id, minutes)
    await callback.answer(f"Интервал: {minutes} мин. ✅")
    await callback.message.edit_text(f"⏱ <b>Интервал обновлен:</b> каждые {minutes} мин.")


@dp.callback_query(F.data.startswith("set_price_"))
async def callback_set_price(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    preset = callback.data.replace("set_price_", "")
    price_map = {
        "0_1m": (None, 1_000_000),
        "1m_3m": (1_000_000, 3_000_000),
        "3m_7m": (3_000_000, 7_000_000),
        "7m_15m": (7_000_000, 15_000_000),
        "15m_up": (15_000_000, None),
        "reset": (None, None),
    }
    min_p, max_p = price_map.get(preset, (None, None))
    database.set_price_filter(chat_id, min_p, max_p)
    price_str = format_price_range(min_p, max_p)
    await callback.answer(f"Цена: {price_str} ✅")
    await callback.message.edit_text(f"💰 <b>Фильтр цен обновлен:</b> {price_str}")


@dp.callback_query(F.data.startswith("set_seller_"))
async def callback_set_seller(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    stype = callback.data.replace("set_seller_", "")
    database.set_seller_type(chat_id, stype)
    name = format_seller_name(stype)
    await callback.answer(f"Продавец: {name} ✅")
    await callback.message.edit_text(f"👤 <b>Тип продавца обновлен:</b> {name}")


@dp.callback_query(F.data.startswith("set_sort_"))
async def callback_set_sort(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    choice = callback.data.replace("set_sort_", "")
    sort_val = "filter_float_price:asc" if choice == "price" else "created_at:desc"
    database.set_sort_by(chat_id, sort_val)
    name = format_sort_name(sort_val)
    await callback.answer(f"Сортировка: {name} ✅")
    await callback.message.edit_text(f"🕒 <b>Сортировка обновлена:</b> {name}")


@dp.callback_query(F.data.startswith("add_stop_"))
async def callback_add_stop_word(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    word = callback.data.replace("add_stop_", "")
    latest = database.get_latest_task(chat_id)
    cur_stops = latest.get("stop_words", "") if latest else ""
    stops_list = [s.strip() for s in cur_stops.split(",") if s.strip()]
    if word not in stops_list:
        stops_list.append(word)
    new_stops = ", ".join(stops_list)
    database.set_stop_words(chat_id, new_stops)
    await callback.answer(f"Добавлено: {word} ✅")
    await callback.message.edit_text(f"🚫 <b>Минус-слова обновлены:</b> <code>{html.escape(new_stops)}</code>")


@dp.callback_query(F.data == "reset_stops")
async def callback_reset_stops(callback: CallbackQuery):
    chat_id = callback.message.chat.id
    database.set_stop_words(chat_id, "")
    await callback.answer("Минус-слова очищены ✅")
    await callback.message.edit_text("🚫 <b>Минус-слова успешно сброшены!</b>")


# --- Process New Query ---

async def process_new_search_query(message: Message, query: str):
    chat_id = message.chat.id

    # Add or update task in database
    task_id = database.save_task(chat_id, query)
    task_data = database.get_task_by_id(task_id, chat_id) or {}

    current_interval = task_data.get("interval_min", DEFAULT_INTERVAL)
    min_p = task_data.get("min_price")
    max_p = task_data.get("max_price")
    loc = task_data.get("location", "list")
    stops = task_data.get("stop_words", "")
    seller = task_data.get("seller_type", "all")
    sort_order = task_data.get("sort_by", "created_at:desc")

    price_str = format_price_range(min_p, max_p)
    loc_str = get_location_name(loc)

    await message.answer(
        f"🔍 <b>Новый поиск добавлен:</b> <i>{html.escape(query)}</i>\n"
        f"📍 <b>Локация:</b> {loc_str}\n"
        f"💰 <b>Цена:</b> {price_str}\n"
        "⚡ Ищу свежие объявления на olx.uz..."
    )

    ads = await asyncio.to_thread(
        parse_olx,
        query,
        3,
        min_p,
        max_p,
        loc,
        stops,
        seller,
        sort_order
    )

    if not ads:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="💰 Цена", callback_data="open_price_menu"),
                    InlineKeyboardButton(text="📍 Город", callback_data="open_location_menu"),
                ]
            ]
        )
        await message.answer(
            f"⚠️ По запросу <b>{html.escape(query)}</b> сейчас ничего не найдено.\n\n"
            f"🔄 Поиск активен в фоне: проверка каждые <b>{current_interval} мин.</b>\n"
            "Все поиски: /tasks | Фильтры: /price, /city, /seller, /stop_words",
            reply_markup=keyboard
        )
        return

    # Send cards with photos and action buttons
    for ad in ads:
        database.add_seen_ad(ad["link"])
        await send_ad_card(
            bot=message.bot,
            chat_id=chat_id,
            ad=ad,
            search_query=query,
            location_name=loc_str
        )
        await asyncio.sleep(0.4)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏱ Интервал", callback_data="open_interval_menu"),
                InlineKeyboardButton(text="💰 Цена", callback_data="open_price_menu"),
                InlineKeyboardButton(text="📍 Город", callback_data="open_location_menu"),
            ],
            [
                InlineKeyboardButton(text="👤 Продавец", callback_data="open_seller_menu"),
                InlineKeyboardButton(text="🚫 Минус-слова", callback_data="open_stops_menu"),
                InlineKeyboardButton(text="📋 Все поиски", callback_data="open_tasks_menu"),
            ]
        ]
    )

    await message.answer(
        f"✅ <b>Первые объявления отправлены!</b>\n\n"
        f"🔄 Мониторинг запущен: проверка каждые <b>{current_interval} мин.</b>\n"
        "Вы можете добавить еще товары — просто отправьте их названия в чат!",
        reply_markup=keyboard
    )


@dp.message(Command("add"))
async def handle_add_command(message: Message, command: CommandObject):
    query = (command.args or "").strip()
    if not query:
        await message.answer("Укажите товар для поиска, например: <code>/add iPhone 14</code>")
        return
    await process_new_search_query(message, query)


@dp.message(F.text & ~F.text.startswith("/"))
async def handle_text_query(message: Message):
    query = (message.text or "").strip()
    if not query:
        await message.answer("Пожалуйста, отправьте непустой текст для поиска.")
        return

    await process_new_search_query(message, query)


# --- 24/7 Background Monitoring Task ---

async def background_monitoring(bot: Bot):
    """
    24/7 background worker running concurrently with bot polling.
    Evaluates due tasks every 15 seconds, scraping fresh ads with individual
    locations, prices, stop words, and seller filters.
    """
    logger.info("24/7 Background monitoring engine started.")

    while True:
        try:
            await asyncio.sleep(15)
            now = time.time()
            due_tasks = database.get_due_tasks(now)

            if not due_tasks:
                continue

            logger.info("Processing %d due tasks...", len(due_tasks))

            for task in due_tasks:
                task_id = task["id"]
                chat_id = task["chat_id"]
                query = task["query"]
                min_p = task.get("min_price")
                max_p = task.get("max_price")
                loc = task.get("location") or "list"
                stops = task.get("stop_words") or ""
                seller = task.get("seller_type") or "all"
                sort_order = task.get("sort_by") or "created_at:desc"
                loc_name = get_location_name(loc)

                # Immediately mark task as checked
                database.update_last_checked(task_id, now)

                try:
                    ads = await asyncio.to_thread(
                        parse_olx,
                        query,
                        None,
                        min_p,
                        max_p,
                        loc,
                        stops,
                        seller,
                        sort_order
                    )

                    new_ads = [ad for ad in ads if not database.is_ad_seen(ad["link"])]

                    if new_ads:
                        logger.info("Found %d new ads for task %s (query: '%s', chat: %s)", len(new_ads), task_id, query, chat_id)

                    for ad in new_ads:
                        database.add_seen_ad(ad["link"])
                        await send_ad_card(
                            bot=bot,
                            chat_id=chat_id,
                            ad=ad,
                            search_query=query,
                            location_name=loc_name
                        )
                        await asyncio.sleep(0.5)

                except TelegramForbiddenError:
                    logger.warning("Bot was blocked by chat %s. Removing tasks.", chat_id)
                    database.delete_all_tasks(chat_id)
                except TelegramBadRequest as e:
                    logger.error("Telegram bad request for chat %s: %s", chat_id, e)
                except Exception as e:
                    logger.error("Error checking query '%s' for chat %s: %s", query, chat_id, e, exc_info=True)

                await asyncio.sleep(2)

        except asyncio.CancelledError:
            logger.info("Background monitoring engine received cancellation.")
            break
        except Exception as e:
            logger.error("Unexpected error in background monitoring engine: %s", e, exc_info=True)


async def main():
    database.init_db()

    if not BOT_TOKEN or BOT_TOKEN == "your_telegram_bot_token_here":
        print("❌ ОШИБКА: Токен бота не задан в .env!")
        sys.exit(1)

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    logger.info("Starting OLX 24/7 Spy Bot with full feature suite...")

    monitor_task = asyncio.create_task(background_monitoring(bot))

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Bot polling active.")
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down bot...")
        monitor_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await monitor_task
        await bot.session.close()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot process exited.")
