import asyncio
import os
import json
import re
import uuid
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, CallbackQuery, ReplyKeyboardMarkup
from dotenv import load_dotenv

from ventebot import VenteBotClient, VenteBotError, extract_products, money

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
DB = os.getenv("DB_PATH", "digistore.db")
BKASH_NUMBER = os.getenv("BKASH_NUMBER", "01785477501")
NAGAD_NUMBER = os.getenv("NAGAD_NUMBER", "01785477501")
BDT_PER_USD = "131"  # Fixed local checkout conversion rate; Binance remains in USD
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
vente = VenteBotClient()

kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="🛍️ Shop Products")],
    [KeyboardButton(text="🔎 Search Products")],
    [KeyboardButton(text="🔥 Special Offers"), KeyboardButton(text="🎁 Refer & Earn")],
    [KeyboardButton(text="📦 My Orders"), KeyboardButton(text="🔎 Track Order")],
    [KeyboardButton(text="💳 Payment Info"), KeyboardButton(text="💬 Support")],
    [KeyboardButton(text="⭐ Reviews"), KeyboardButton(text="👤 My Account")],
    [KeyboardButton(text="🌐 Visit Website"), KeyboardButton(text="📢 Main Channel")],
], resize_keyboard=True)

dp = Dispatcher()

# Storebat catalog display order supplied by the admin.
# Products still come live from VenteBot API; this only controls their serial/order.
STOREBAT_PRODUCT_ORDER = [
    "Gemini 18 months",
    "ChatGPT - GPT Plus K12 edu 24M (NW)",
    "Google AI Pro 12m FW",
    "Grok - Super Grok 9-10 Days (W5D)",
    "Capcut pro 1M FW",
    "Capcut pro 1M 1600 iA credit FW",
    "Capcut 6month FW individual",
    "Descript Creator 1 Year",
    "Cousera - Bussiness 6m (ready account)",
    "Gamma AI - Plus 1M (W25D)",
    "Codex API",
    "Claude API",
    "Notion 1M-12M",
    "VPNs nord,proton,HMA,surfshark...",
    "Microsoft Office 365 Plus 1 year",
    "MIRO",
    "zoom Pro",
    "Figma Pro Edu 2yrs",
    "iLovePdf Premium 1Yr",
    "Adobe Express Premium (12 Months)",
    "Amazon prime 6 months video 6 profile",
    "Peacock official subscriptions 1year",
    "Wink - Wink Smile+ 7D (W5D)",
    "JetBrains EDU 1 year",
    "Autodesk Admin Dashboard Access(3000 invitation)",
    "Quizlet",
    "wordwall PRO",
    "Brain.fm 1 Year",
    "Quillbot - 1 month",
    "customer.io essentials 1year",
    "Fin ia agent +fin advanced",
    "Factory 12m",
    "Framer Pro 12m",
    "Granola Business 12M",
    "Gumloop Pro 12m",
    "Jam Team 10 Seat 12m",
    "Linear business 1 year",
    "Mobbin 10x Seat 12m",
    "Posthog scale 2x monthly limits",
    "Railway hobby 1 year",
    "readwise 1 Year",
    "Resend Pro 12m",
    "Supercut Pro 10 Seat 12m",
    "Wispr Flow Pro 12m",
    "Magic Patterns Starter 12m",
    "Warp build 1 year",
    "Gamma Pro 12m",
    "Elevenlabs - Free 10k Credits (W24H)",
    "ElevenLabs Creator 12m",
    "Replit Core 12m",
    "Runway Pro 12m",
    "Supabase Pro",
    "Lovable Pro 12m",
    "N8N Starter 12m",
    "Cursor Pro 12m",
    "Manus Pro 1 Year",
    "Snapchat Plus+ 3M FW available",
    "Snapchat Plus+ 6M FW available",
    "Claude 100$ Api 30 D warranty",
    "Claude 500$ Api 30 Days",
]

# Admin-approved selling prices for newly added products.
# A price saved later through Product Prices still overrides these defaults.
DEFAULT_SELLING_PRICES = {
    "Claude 100$ Api 30 D warranty": Decimal("3.00"),
    "Claude 500$ Api 30 Days": Decimal("7.50"),
}

def _catalog_key(text: str) -> str:
    return "".join(ch.lower() for ch in str(text) if ch.isalnum())

def canonical_storebat_name(text: str):
    key = _catalog_key(text)
    for wanted in STOREBAT_PRODUCT_ORDER:
        wanted_key = _catalog_key(wanted)
        if key == wanted_key or (wanted_key and (key.startswith(wanted_key) or wanted_key.startswith(key))):
            return wanted
    return None

def sort_storebat_products(items):
    # Strict allow-list: only the 60 products approved by the admin are shown.
    order = {_catalog_key(name): i for i, name in enumerate(STOREBAT_PRODUCT_ORDER)}
    kept = []
    for p in items:
        supplier_name = str(p.get("name") or p.get("title") or "")
        canonical = canonical_storebat_name(supplier_name)
        if canonical is None:
            continue
        kept.append((order[_catalog_key(canonical)], p))
    kept.sort(key=lambda x: x[0])
    return [p for _, p in kept]


ADMIN_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📦 Product Management", callback_data="admin:products_manage")],
    [InlineKeyboardButton(text="📦 All Orders", callback_data="admin:all_orders:0")],
    [InlineKeyboardButton(text="📦 Pending Payments", callback_data="admin:pending")],
    [InlineKeyboardButton(text="🎁 Referral Campaigns", callback_data="admin:ref_help")],
    [InlineKeyboardButton(text="💳 Payment Settings", callback_data="admin:payment_help")],
    [InlineKeyboardButton(text="🔥 Offers", callback_data="admin:offer_help"), InlineKeyboardButton(text="💬 Support", callback_data="admin:support_help")],
    [InlineKeyboardButton(text="📈 Sales Dashboard", callback_data="admin:sales_dashboard")],
    [InlineKeyboardButton(text="📊 Statistics", callback_data="admin:stats"), InlineKeyboardButton(text="📢 Broadcast", callback_data="admin:broadcast_help")],
    [InlineKeyboardButton(text="🔄 Refresh Order Status", callback_data="admin:status_help")],
])

async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.executescript('''
        CREATE TABLE IF NOT EXISTS users(
          telegram_id INTEGER PRIMARY KEY, username TEXT, full_name TEXT,
          referrer_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS managed_products(product_id TEXT PRIMARY KEY, display_name TEXT, enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS prices(
          product_id TEXT PRIMARY KEY, selling_price_usd TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders(
          id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL,
          product_id TEXT NOT NULL, product_name TEXT, quantity INTEGER DEFAULT 1,
          amount_usd TEXT, supplier_order_id TEXT, status TEXT DEFAULT 'awaiting_payment',
          activation_identifier TEXT, payment_reference TEXT, payment_method TEXT DEFAULT 'binance',
          idempotency_key TEXT UNIQUE, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS referral_campaigns(
          id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT NOT NULL,
          product_name TEXT, referrals_required INTEGER NOT NULL, reward_type TEXT NOT NULL,
          special_price_usd TEXT, enabled INTEGER DEFAULT 1,
          starts_at TEXT, ends_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS referral_redemptions(
          campaign_id INTEGER NOT NULL, telegram_id INTEGER NOT NULL,
          order_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(campaign_id, telegram_id)
        );
        CREATE TABLE IF NOT EXISTS settings(
          key TEXT PRIMARY KEY, value TEXT
        );
        CREATE TABLE IF NOT EXISTS offers(
          id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT NOT NULL,
          product_name TEXT, offer_price_usd TEXT NOT NULL, enabled INTEGER DEFAULT 1,
          starts_at TEXT, ends_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS user_input_state(
          telegram_id INTEGER PRIMARY KEY, action TEXT NOT NULL, order_id INTEGER,
          product_id TEXT, unit_price_usd TEXT, product_name TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_payment_reference
          ON orders(payment_reference) WHERE payment_reference IS NOT NULL;
        ''')
        # Safe upgrades from older DB versions.
        cols = {r[1] for r in await (await db.execute("PRAGMA table_info(orders)")).fetchall()}
        for name, ddl in {
            "product_name": "TEXT", "activation_identifier": "TEXT",
            "payment_reference": "TEXT", "payment_method": "TEXT DEFAULT 'binance'", "idempotency_key": "TEXT"
        }.items():
            if name not in cols:
                await db.execute(f"ALTER TABLE orders ADD COLUMN {name} {ddl}")
        ref_cols = {r[1] for r in await (await db.execute("PRAGMA table_info(referral_campaigns)")).fetchall()}
        for name, ddl in {"product_name":"TEXT", "starts_at":"TEXT", "ends_at":"TEXT"}.items():
            if name not in ref_cols:
                await db.execute(f"ALTER TABLE referral_campaigns ADD COLUMN {name} {ddl}")
        managed_cols = {r[1] for r in await (await db.execute("PRAGMA table_info(managed_products)")).fetchall()}
        if "pin_order" not in managed_cols:
            await db.execute("ALTER TABLE managed_products ADD COLUMN pin_order INTEGER NOT NULL DEFAULT 0")
        await db.commit()

async def get_setting(key: str, default=None):
    async with aiosqlite.connect(DB) as db:
        row = await (await db.execute("SELECT value FROM settings WHERE key=?", (key,))).fetchone()
    return row[0] if row else default

async def set_setting_value(key: str, value: str):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        await db.commit()

async def referral_count(user_id: int):
    async with aiosqlite.connect(DB) as db:
        return (await (await db.execute("SELECT COUNT(*) FROM users WHERE referrer_id=?", (user_id,))).fetchone())[0]

async def custom_price(product_id: str):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT selling_price_usd FROM prices WHERE product_id=?", (str(product_id),))
        row = await cur.fetchone()
    return money(row[0]) if row else None

async def selling_price_for_product(product: dict):
    # Saved admin price wins. For newly approved catalog items, use the
    # explicitly approved default selling price until the admin changes it.
    pid = product.get("id") or product.get("product_id") or product.get("uuid")
    saved = await custom_price(str(pid)) if pid is not None else None
    if saved is not None:
        return saved
    canonical = canonical_storebat_name(str(product.get("name") or product.get("title") or ""))
    return DEFAULT_SELLING_PRICES.get(canonical)

async def display_price(product: dict):
    # Customer-facing price: ONLY the admin-defined/approved selling price.
    # Supplier cost must never leak to customers.
    return await selling_price_for_product(product)

def product_family(name: str) -> str:
    """Group variants by the leading product/brand name, ignoring case and punctuation."""
    words = re.findall(r"[a-z0-9]+", str(name).casefold())
    return words[0] if words else ""


def group_products_by_name(items, overrides):
    """Keep pinned groups first, then group same-name variants in original order."""
    groups = {}
    first_seen = {}
    for index, item in enumerate(items):
        pid = str(item.get("id") or item.get("product_id") or item.get("uuid") or "")
        family = product_family(item.get("name") or item.get("title") or "")
        key = family or ("product:" + pid)
        groups.setdefault(key, []).append(item)
        first_seen.setdefault(key, index)

    def pin_value(item):
        pid = str(item.get("id") or item.get("product_id") or item.get("uuid") or "")
        return overrides.get(pid, (None, 1, 0))[2] or 0

    ordered = sorted(groups, key=lambda key: (
        -max(pin_value(item) for item in groups[key]),
        first_seen[key]
    ))
    result = []
    for key in ordered:
        result.extend(sorted(groups[key], key=lambda item: -pin_value(item)))
    return result


async def catalog_products():
    """Preserve original approved products, with admin-added supplier IDs and visibility overrides."""
    raw = extract_products(await vente.products(lang="en"))
    async with aiosqlite.connect(DB) as db:
        rows = await (await db.execute("SELECT product_id,display_name,enabled,pin_order FROM managed_products")).fetchall()
    overrides = {str(pid): (name, enabled, pin_order) for pid, name, enabled, pin_order in rows}
    selected = []
    for product in raw:
        pid = str(product.get("id") or product.get("product_id") or product.get("uuid") or "")
        original = str(product.get("name") or product.get("title") or "")
        record = overrides.get(pid)
        if record and not record[1]:
            continue
        if canonical_storebat_name(original) or (record and record[1]):
            item = dict(product)
            item["name"] = (record[0] if record and record[0] else canonical_storebat_name(original) or original)
            selected.append(item)
    # Same-name variants stay together in both customer and admin listings.
    # A pinned product brings its whole family to the top.
    return group_products_by_name(selected, overrides)


async def get_product(product_id: str):
    data = await vente.products(lang="en")
    for p in extract_products(data):
        pid = str(p.get("id") or p.get("product_id") or p.get("uuid") or "")
        if pid == str(product_id):
            return p
    return None

# Main-menu labels route to existing handlers; existing business logic is unchanged.
@dp.message(F.text == "🛍️ Shop Products")
async def menu_shop_products(m: Message):
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM user_input_state WHERE telegram_id=? AND action LIKE 'admin_%'", (m.from_user.id,))
        await db.commit()
    await products(m)

@dp.message(F.text == "🔥 Special Offers")
async def menu_special_offers(m: Message):
    await offers(m)

@dp.message(F.text == "🎁 Refer & Earn")
async def menu_refer_earn(m: Message):
    await referrals(m)

@dp.message(F.text == "💳 Payment Info")
async def menu_payment_info(m: Message):
    await payment(m)

@dp.message(F.text == "📢 Main Channel")
async def menu_main_channel(m: Message):
    await m.answer("📢 Join our Main Channel:", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📢 Open Main Channel", url="https://t.me/ismaildigistore")]]))

@dp.message(F.text == "🌐 Visit Website")
async def menu_visit_website(m: Message):
    await m.answer("🌐 Visit our website:", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🌐 Open Website", url="https://ismaildigistore.xyz/")]]))

@dp.message(F.text == "👤 My Account")
async def menu_my_account(m: Message):
    async with aiosqlite.connect(DB) as db:
        row = await (await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END),0) FROM orders WHERE telegram_id=?",
            (m.from_user.id,))).fetchone()
    await m.answer(f"👤 My Account\\nID: {m.from_user.id}\\nOrders: {row[0]}\\nDelivered: {row[1]}")

@dp.message(F.text == "🔎 Track Order")
async def menu_track_order(m: Message):
    async with aiosqlite.connect(DB) as db:
        rows=await (await db.execute("SELECT id,product_name,status FROM orders WHERE telegram_id=? ORDER BY id DESC LIMIT 5",(m.from_user.id,))).fetchall()
    if not rows: return await m.answer("You have no orders yet.")
    buttons=[[InlineKeyboardButton(text=f"#{oid} • {str(name or 'Product')[:27]} • {customer_status_label(status)}",callback_data=f"customer:track:{oid}")] for oid,name,status in rows]
    await m.answer("🔎 Select an order, or send /track ORDER_ID:",reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("customer:track:"))
async def customer_track_button(c: CallbackQuery):
    oid=int(c.data.rsplit(":",1)[1])
    await show_customer_order(c.message if False else _CustomerMessage(c),oid)
    await c.answer()

class _CustomerMessage:
    def __init__(self,c): self.from_user=c.from_user; self.answer=c.message.answer


@dp.message(Command("track"))
async def menu_track_command(m: Message):
    parts=(m.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit(): return await m.answer("Use /track ORDER_ID")
    await show_customer_order(m,int(parts[1]))

@dp.message(F.text == "⭐ Reviews")
async def menu_reviews(m: Message):
    await m.answer("⭐ Customer reviews are on our Telegram channel:", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⭐ Open Reviews Channel", url="https://t.me/ismaildigistore")]]))

@dp.message(CommandStart())
async def start(m: Message):
    parts = (m.text or "").split(maxsplit=1)
    referrer = None
    if len(parts) == 2 and parts[1].startswith("ref_"):
        try: referrer = int(parts[1][4:])
        except ValueError: pass
    if referrer == m.from_user.id: referrer = None
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT OR IGNORE INTO users(telegram_id,username,full_name,referrer_id) VALUES(?,?,?,?)",
                         (m.from_user.id, m.from_user.username, m.from_user.full_name, referrer))
        await db.commit()
    await m.answer("Welcome to Ismail Digistore! 🛍️\nChoose an option below.", reply_markup=kb)

# Optional verified Telegram custom-emoji IDs for real company logos.
# JSON mapping of company name -> Telegram custom emoji ID.
# No placeholder IDs are sent to Telegram.
def company_logo_id(product_name: str):
    try:
        logos = json.loads(os.getenv("PRODUCT_LOGO_EMOJI_IDS", "{}"))
    except (ValueError, TypeError):
        return None
    if not isinstance(logos, dict):
        return None
    name = _catalog_key(product_name)
    for company, emoji_id in sorted(logos.items(), key=lambda kv: len(str(kv[0])), reverse=True):
        if _catalog_key(company) in name and str(emoji_id).isdigit():
            return str(emoji_id)
    return None

async def product_list_markup():
    items = await catalog_products()
    rows = []
    last_category = None
    for p in items:
        pid = str(p.get("id") or p.get("product_id") or p.get("uuid") or "")
        if not pid:
            continue
        name = str(p.get("name") or p.get("title") or "Product")
        category = api_category_header(name)
        if category and category != last_category:
            rows.append([InlineKeyboardButton(text=category, callback_data="products:category_noop")])
        last_category = category
        price = await display_price(p)
        price_text = f"${price:.2f}" if isinstance(price, Decimal) else "Unavailable"
        logo_id = company_logo_id(name)
        prefix = "" if logo_id else (p.get("emoji") or "📦") + " "
        label = f"{prefix}{name} — {price_text}"
        if len(label) > 58:
            label = f"{prefix}{name[:35].rstrip()}… — {price_text}"
        button_options = {"text": label, "callback_data": f"product:{pid}"}
        if logo_id:
            button_options["icon_custom_emoji_id"] = logo_id
        rows.append([InlineKeyboardButton(**button_options)])
    rows.append([InlineKeyboardButton(text="🔄 Refresh", callback_data="products:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@dp.message(F.text == "🛍️ Products")
async def products(m: Message):
    try:
        markup = await product_list_markup()
    except VenteBotError as e:
        await m.answer(f"⚠️ Product catalog is not available yet.\n{e}")
        return
    await m.answer("🛍️ <b>Ismail Digistore</b>\nChoose a product below:", parse_mode="HTML", reply_markup=markup)

@dp.callback_query(F.data == "products:category_noop")
async def products_category_noop(c: CallbackQuery):
    await c.answer()

@dp.callback_query(F.data == "products:refresh")
async def products_refresh(c: CallbackQuery):
    try:
        markup = await product_list_markup()
        await c.message.edit_text("🛍️ <b>Ismail Digistore</b>\nChoose a product below:", parse_mode="HTML", reply_markup=markup)
        await c.answer("Products refreshed")
    except VenteBotError as e:
        await c.answer(str(e), show_alert=True)

@dp.callback_query(F.data == "products:back")
async def products_back(c: CallbackQuery):
    try:
        markup = await product_list_markup()
        await c.message.edit_text("🛍️ <b>Ismail Digistore</b>\nChoose a product below:", parse_mode="HTML", reply_markup=markup)
    except VenteBotError as e:
        await c.answer(str(e), show_alert=True)
        return
    await c.answer()

def clean_supplier_text(value):
    import html
    import re
    text = str(value or "")
    # Remove Telegram custom-emoji markup together with its fallback emoji.
    text = re.sub(r'<tg-emoji\b[^>]*>.*?</tg-emoji>', '', text, flags=re.IGNORECASE | re.DOTALL)
    # Remove ordinary emoji/pictographs supplied in product text.
    text = re.sub(r'[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]', '', text)
    # Clean spacing left behind by removed markup/emoji.
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return html.escape(text)

@dp.callback_query(F.data.startswith("product:"))
async def product_details(c: CallbackQuery):
    pid = c.data.split(":", 1)[1]
    try:
        p = await get_product(pid)
    except VenteBotError as e:
        await c.answer(str(e), show_alert=True)
        return
    if not p:
        await c.answer("Product is no longer available.", show_alert=True)
        return
    name = clean_supplier_text(p.get("name") or p.get("title") or "Product")
    description = clean_supplier_text(p.get("description") or "")
    stock = p.get("stock")
    price = await display_price(p)
    price_text = f"${price:.2f}" if isinstance(price, Decimal) else "Unavailable"
    text = f"<b>{name}</b>\n💵 Price: {price_text}"
    if stock is not None:
        text += f"\n📦 Stock: {stock}"
    if description:
        text += f"\n\n{description}"
    rows = []
    if isinstance(price, Decimal):
        rows.append([InlineKeyboardButton(text="🛒 Buy", callback_data=f"buy:{pid}")])
    else:
        text += "\n\n⚠️ This product is not on sale yet."
    rows.append([InlineKeyboardButton(text="⬅️ Back to Products", callback_data="products:back")])
    await c.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()

@dp.callback_query(F.data.startswith("buy:"))
async def buy_product(c: CallbackQuery):
    pid = c.data.split(":", 1)[1]
    try: p = await get_product(pid)
    except VenteBotError as e: await c.message.answer(f"⚠️ {e}"); return await c.answer()
    if not p: await c.message.answer("Product is no longer available."); return await c.answer()
    price = await display_price(p)
    if price is None:
        await c.message.answer("⚠️ This product is not on sale yet. Please contact support.")
        return await c.answer()
    name = p.get("name") or p.get("title") or "Product"
    qkb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1", callback_data=f"qty:{pid}:1"),
         InlineKeyboardButton(text="2", callback_data=f"qty:{pid}:2"),
         InlineKeyboardButton(text="3", callback_data=f"qty:{pid}:3")],
        [InlineKeyboardButton(text="Custom", callback_data=f"qtycustom:{pid}")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="order:cancel")],
    ])
    await c.message.answer(f"📦 <b>{name}</b>\n💵 Unit price: ${price:.2f}\n\nChoose quantity:", parse_mode="HTML", reply_markup=qkb)
    await c.answer()

async def create_customer_order(c: CallbackQuery, pid: str, qty: int):
    try: p = await get_product(pid)
    except VenteBotError as e: return await c.message.answer(f"⚠️ {e}")
    if not p: return await c.message.answer("Product is no longer available.")
    price = await display_price(p)
    if price is None: return await c.message.answer("⚠️ This product is not on sale yet.")
    stock = p.get("stock")
    try:
        if stock is not None and int(stock) < qty: return await c.message.answer(f"⚠️ Only {stock} item(s) are in stock.")
    except (TypeError, ValueError): pass
    name = p.get("name") or p.get("title") or "Product"
    total = price * qty
    idem = str(uuid.uuid4())
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("INSERT INTO orders(telegram_id,product_id,product_name,quantity,amount_usd,idempotency_key) VALUES(?,?,?,?,?,?)",
                               (c.from_user.id, pid, name, qty, f"{total:.2f}", idem))
        oid = cur.lastrowid; await db.commit()
    for admin_id in ADMIN_IDS:
        try:
            await c.message.bot.send_message(admin_id, f"🆕 New order #{oid}\n📦 {name}\n🔢 Qty: {qty}\n💵 ${total:.2f}\n👤 Customer: {c.from_user.id}\n⏳ Awaiting payment")
        except Exception: pass
    await c.message.answer(
        f"🛒 <b>New Order</b>\\n🧾 Order: #{oid}\\n📦 Product: {name}\\n🔢 Quantity: {qty}\\n💰 Total: ${total:.2f}\\n\\n"
        "💳 Select your payment method:",
        parse_mode="HTML", reply_markup=payment_method_keyboard(oid))

def payment_method_keyboard(oid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 International Payment — Binance Pay", callback_data=f"method:{oid}:binance")],
        [InlineKeyboardButton(text="🇧🇩 Local Payment — bKash", callback_data=f"method:{oid}:bkash"),
         InlineKeyboardButton(text="🇧🇩 Local Payment — Nagad", callback_data=f"method:{oid}:nagad")],
        [InlineKeyboardButton(text="❌ Cancel Order", callback_data=f"cancel:{oid}")]
    ])

def local_total(amount_usd):
    try:
        rate = Decimal(BDT_PER_USD)
        if not rate.is_finite() or rate <= 0: return None
        return (Decimal(str(amount_usd)) * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None

@dp.callback_query(F.data.startswith("method:"))
async def select_payment_method(c: CallbackQuery):
    _, oid_text, method = c.data.split(":")
    oid = int(oid_text)
    if method not in ("binance", "bkash", "nagad"):
        return await c.answer("Invalid payment method.", show_alert=True)
    async with aiosqlite.connect(DB) as db:
        row = await (await db.execute("SELECT amount_usd FROM orders WHERE id=? AND telegram_id=? AND status='awaiting_payment'", (oid,c.from_user.id))).fetchone()
        if not row: return await c.answer("Order not awaiting payment.", show_alert=True)
        if method != "binance" and local_total(row[0]) is None:
            return await c.answer("Local payment rate is not configured. Please contact support.", show_alert=True)
        await db.execute("UPDATE orders SET payment_method=? WHERE id=? AND telegram_id=? AND status='awaiting_payment'", (method,oid,c.from_user.id))
        await db.commit()
    if method == "binance":
        instructions = await get_setting("payment_instructions", "Binance Pay details are not configured yet. Please contact support.")
        details = f"🌍 <b>International Payment — Binance Pay</b>\\n💰 Amount: ${row[0]}\\n\\n{instructions}"
    else:
        name, number = ("bKash", BKASH_NUMBER) if method == "bkash" else ("Nagad", NAGAD_NUMBER)
        details = (f"🇧🇩 <b>Local Payment — {name}</b>\\n"
                   f"💰 Send Money amount: ৳{local_total(row[0])}\\n"
                   f"📱 Personal number: <code>{number}</code>\\n\\n"
                   "Send Money to the number above, then submit your Transaction ID. "
                   "Payment must be verified by the admin before delivery.")
    keys = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ I Paid — Submit Transaction ID", callback_data=f"payref:{oid}")],
        [InlineKeyboardButton(text="⬅️ Change Method", callback_data=f"methods:{oid}")]])
    await c.message.answer(f"🧾 Order #{oid}\\n\\n{details}", parse_mode="HTML", reply_markup=keys)
    await c.answer()

@dp.callback_query(F.data.startswith("methods:"))
async def change_payment_method(c: CallbackQuery):
    oid=int(c.data.split(":")[1])
    async with aiosqlite.connect(DB) as db:
        row=await (await db.execute("SELECT 1 FROM orders WHERE id=? AND telegram_id=? AND status='awaiting_payment'",(oid,c.from_user.id))).fetchone()
    if not row: return await c.answer("Order not awaiting payment.", show_alert=True)
    await c.message.edit_text("💳 Select Payment Method:",reply_markup=payment_method_keyboard(oid))
    await c.answer()

@dp.callback_query(F.data.startswith("qty:"))
async def choose_quantity(c: CallbackQuery):
    _, pid, qty = c.data.split(":", 2)
    await create_customer_order(c, pid, int(qty))
    await c.answer()

@dp.callback_query(F.data.startswith("qtycustom:"))
async def custom_quantity(c: CallbackQuery):
    pid = c.data.split(":", 1)[1]
    try: p = await get_product(pid)
    except VenteBotError as e: await c.message.answer(f"⚠️ {e}"); return await c.answer()
    price = await display_price(p) if p else None
    if not p or price is None: await c.message.answer("Product/price is unavailable."); return await c.answer()
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT INTO user_input_state(telegram_id,action,product_id,unit_price_usd,product_name) VALUES(?,?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET action=excluded.action,order_id=NULL,product_id=excluded.product_id,unit_price_usd=excluded.unit_price_usd,product_name=excluded.product_name,created_at=CURRENT_TIMESTAMP",
                         (c.from_user.id,"custom_qty",pid,f"{price:.2f}",p.get("name") or p.get("title") or "Product")); await db.commit()
    await c.message.answer("🔢 Send the quantity you want as a number (example: 8).")
    await c.answer()

@dp.callback_query(F.data == "order:cancel")
async def cancel_menu(c: CallbackQuery):
    await c.message.answer("❌ Purchase cancelled.")
    await c.answer()

@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_order(c: CallbackQuery):
    oid=int(c.data.split(":",1)[1])
    async with aiosqlite.connect(DB) as db:
        cur=await db.execute("UPDATE orders SET status='cancelled' WHERE id=? AND telegram_id=? AND status='awaiting_payment'",(oid,c.from_user.id)); await db.commit()
    await c.message.answer("❌ Order cancelled." if cur.rowcount else "This order can no longer be cancelled.")
    await c.answer()

@dp.callback_query(F.data.startswith("payref:"))
async def ask_payment_reference(c: CallbackQuery):
    oid=int(c.data.split(":",1)[1])
    async with aiosqlite.connect(DB) as db:
        row=await (await db.execute("SELECT payment_method FROM orders WHERE id=? AND telegram_id=? AND status='awaiting_payment'",(oid,c.from_user.id))).fetchone()
        if not row: await c.answer("Order is not awaiting payment.",show_alert=True); return
        await db.execute("INSERT INTO user_input_state(telegram_id,action,order_id) VALUES(?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET action=excluded.action,order_id=excluded.order_id,product_id=NULL,unit_price_usd=NULL,product_name=NULL,created_at=CURRENT_TIMESTAMP",(c.from_user.id,"payment_ref",oid)); await db.commit()
    await c.message.answer(f"💳 Send the {dict(binance='Binance Transaction ID / Order ID', bkash='bKash Transaction ID', nagad='Nagad Transaction ID').get(row[0] or 'binance', 'Transaction ID')} for Order #{oid} in the message box now.")
    await c.answer()

@dp.message(Command("activate"))
async def activate(m: Message):
    parts = (m.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        return await m.answer("Use: /activate ORDER_ID EMAIL_OR_USERNAME")
    oid, identifier = int(parts[1]), parts[2].strip()
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("UPDATE orders SET activation_identifier=? WHERE id=? AND telegram_id=? AND status IN ('awaiting_payment','payment_submitted')", (identifier, oid, m.from_user.id))
        await db.commit()
    await m.answer("✅ Activation identifier saved." if cur.rowcount else "⚠️ Order not found or can no longer be changed.")

@dp.message(Command("admin"))
async def admin_panel(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    await m.answer("⚙️ Ismail Digistore Admin", reply_markup=ADMIN_KB)

def api_category_header(name: str):
    """Only label Claude and Codex API products; leave all other products untouched."""
    lowered = name.casefold()
    if "api" not in lowered and "token" not in lowered:
        return None
    if "claude" in lowered:
        return "📂 Claude API"
    if "codex" in lowered:
        return "📂 Codex API"
    return None


async def show_admin_prices(c: CallbackQuery, page: int = 0):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    try:
        items = await catalog_products()
    except VenteBotError as e:
        await c.message.answer(f"⚠️ Could not load products: {e}")
        return await c.answer()
    if not items:
        await c.message.answer("🛍️ No products are currently available.")
        return await c.answer()

    page_size = 10
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))
    page_items = items[page * page_size:(page + 1) * page_size]

    rows = []
    last_category = None
    for p in page_items:
        category = api_category_header(str(p.get("name") or p.get("title") or ""))
        if category and category != last_category:
            rows.append([InlineKeyboardButton(text=category, callback_data="admin:prices_noop")])
        last_category = category
        pid = str(p.get("id") or p.get("product_id") or p.get("uuid") or "")
        if not pid:
            continue
        name = str(p.get("name") or p.get("title") or "Product")
        selling = await selling_price_for_product(p)
        selling_text = f"${selling:.2f}" if isinstance(selling, Decimal) else "Not set"
        stock = p.get("stock")
        stock_text = f"📦 {stock}" if stock is not None else "📦 —"
        emoji = p.get("emoji") or "📦"
        label = f"{emoji} {name} | {selling_text} | {stock_text}"
        if len(label) > 60:
            label = f"{emoji} {name[:30].rstrip()}… | {selling_text} | {stock_text}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:price_item:{pid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"admin:prices_page:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="admin:prices_noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"admin:prices_page:{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔎 Search Product", callback_data="admin:prices_search")])

    text = (
        "💵 <b>Product Prices</b>\n\n"
        "Tap a product, then send only the new USD selling price.\n"
        f"Products: <b>{len(items)}</b> • Page <b>{page+1}/{total_pages}</b>"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    if c.data.startswith("admin:prices_page:"):
        await c.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await c.message.answer(text, parse_mode="HTML", reply_markup=markup)
    await c.answer()

@dp.callback_query(F.data == "admin:products_manage")
async def admin_products_manage(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 All Products / Edit", callback_data="admin:product_edit")],
        [InlineKeyboardButton(text="➕ Add New Product", callback_data="admin:product_add")],
        [InlineKeyboardButton(text="🔎 Search Product", callback_data="admin:edit_search")],
        [InlineKeyboardButton(text="💵 Product Prices", callback_data="admin:prices")],
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin:home")],
    ])
    await c.message.answer("📦 <b>Product Management</b>\nChoose an option below.\nTo edit a product's price, open All Products / Edit and select the product.", parse_mode="HTML", reply_markup=kb)
    await c.answer()


@dp.callback_query(F.data == "admin:product_add")
async def admin_product_add(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    await set_admin_product_state(c.from_user.id, "admin_add_search")
    await c.message.answer("➕ Send a supplier product name to search VenteBot catalog. You can only add products that exist at the supplier.")
    await c.answer()


@dp.callback_query(F.data == "admin:product_edit")
async def admin_product_edit(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    await show_admin_edit_products(c, 0)


async def show_admin_edit_products(c: CallbackQuery, page: int = 0):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    try:
        items = await catalog_products()
    except VenteBotError as e:
        await c.message.answer(f"⚠️ Could not load products: {e}")
        return await c.answer()
    if not items:
        await c.message.answer("No products are available for editing.")
        return await c.answer()
    size = 10
    pages = max(1, (len(items) + size - 1) // size)
    page = max(0, min(page, pages - 1))
    rows = []
    last_category = None
    for product in items[page * size:(page + 1) * size]:
        category = api_category_header(str(product.get("name") or product.get("title") or ""))
        if category and category != last_category:
            rows.append([InlineKeyboardButton(text=category, callback_data="admin:prices_noop")])
        last_category = category
        pid = str(product.get("id") or product.get("product_id") or product.get("uuid") or "")
        if not pid:
            continue
        name = str(product.get("name") or product.get("title") or "Product")
        rows.append([InlineKeyboardButton(text=f"✏️ {name[:52]}", callback_data=f"admin:manage:edit:{pid}")])
    nav = []
    if page:
        nav.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"admin:edit_page:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page+1}/{pages}", callback_data="admin:prices_noop"))
    if page + 1 < pages:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"admin:edit_page:{page+1}"))
    rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔎 Search instead", callback_data="admin:edit_search")])
    text = f"✏️ Edit Products ({len(items)} total) — Page {page+1}/{pages}\nTap a product to change its name or price."
    # Replace the current Telegram message instead of sending a new message on Next/Previous.
    await c.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


@dp.callback_query(F.data.startswith("admin:edit_page:"))
async def admin_edit_page(c: CallbackQuery):
    await show_admin_edit_products(c, int(c.data.rsplit(":", 1)[-1]))


@dp.callback_query(F.data == "admin:edit_search")
async def admin_edit_search(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    await set_admin_product_state(c.from_user.id, "admin_edit_search")
    await c.message.answer("🔎 Send a product name to search for editing.")
    await c.answer()


async def set_admin_product_state(uid, action):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO user_input_state(telegram_id,action) VALUES(?,?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET action=excluded.action,product_id=NULL,"
            "order_id=NULL,created_at=CURRENT_TIMESTAMP", (uid, action))
        await db.commit()


async def admin_manage_search(m, term, mode):
    if m.from_user.id not in ADMIN_IDS:
        return
    if len(term) < 2:
        return await m.answer("Enter at least 2 characters.")
    try:
        products = extract_products(await vente.products(lang="en")) if mode == "add" else await catalog_products()
    except VenteBotError:
        return await m.answer("⚠️ Supplier catalog unavailable.")
    rows = []
    for product in products:
        pid = str(product.get("id") or product.get("product_id") or product.get("uuid") or "")
        name = str(product.get("name") or product.get("title") or "")
        if pid and term.casefold() in name.casefold():
            rows.append([InlineKeyboardButton(text=name[:55], callback_data=f"admin:manage:{mode}:{pid}")])
            if len(rows) >= 15:
                break
    if not rows:
        return await m.answer("No matching supplier products. Try another name.")
    await m.answer("Select a product:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("admin:manage:"))
async def admin_manage_select(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    _, _, mode, pid = c.data.split(":", 3)
    try:
        p = await get_product(pid)
    except VenteBotError:
        return await c.answer("Supplier API unavailable", show_alert=True)
    if not p:
        return await c.answer("Supplier product not found", show_alert=True)
    name = str(p.get("name") or p.get("title") or "Product")
    if mode == "add":
        async with aiosqlite.connect(DB) as db:
            await db.execute("INSERT INTO managed_products(product_id,display_name,enabled) VALUES(?,?,1) "
                             "ON CONFLICT(product_id) DO UPDATE SET enabled=1", (pid, name))
            await db.commit()
        await c.message.answer(f"✅ Product added to your catalog: {name}\\nSet its selling price from Product Management before selling.")
    elif mode == "edit":
        async with aiosqlite.connect(DB) as db:
            pin_row = await (await db.execute("SELECT pin_order FROM managed_products WHERE product_id=?", (pid,))).fetchone()
        is_pinned = bool(pin_row and pin_row[0])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💵 Edit Price", callback_data=f"admin:price_item:{pid}")],
            [InlineKeyboardButton(text="✏️ Rename Product", callback_data=f"admin:rename:{pid}")],
            [InlineKeyboardButton(text="↩️ Remove from Top" if is_pinned else "📌 Pin to Top", callback_data=f"admin:unpin:{pid}" if is_pinned else f"admin:pin:{pid}")],
            [InlineKeyboardButton(text="🙈 Hide Product", callback_data=f"admin:hide:{pid}")]])
        await c.message.answer(f"✏️ Edit: {name}", reply_markup=keyboard)
    await c.answer()


@dp.callback_query(F.data.startswith("admin:pin:") | F.data.startswith("admin:unpin:"))
async def admin_pin_product(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    action, pid = c.data.split(":", 2)[1:]
    try:
        product = await get_product(pid)
    except VenteBotError:
        return await c.answer("Supplier API unavailable", show_alert=True)
    if not product:
        return await c.answer("Product not found", show_alert=True)
    async with aiosqlite.connect(DB) as db:
        if action == "pin":
            row = await (await db.execute("SELECT COALESCE(MAX(pin_order), 0) FROM managed_products")).fetchone()
            next_order = int(row[0]) + 1
            await db.execute("INSERT INTO managed_products(product_id,pin_order) VALUES(?,?) "
                             "ON CONFLICT(product_id) DO UPDATE SET pin_order=excluded.pin_order", (pid, next_order))
        else:
            await db.execute("UPDATE managed_products SET pin_order=0 WHERE product_id=?", (pid,))
        await db.commit()
    label = "📌 Pinned to top" if action == "pin" else "↩️ Removed from top"
    await c.message.edit_text(f"{label}: {str(product.get('name') or product.get('title') or 'Product')}\nReturn to All Products / Edit to continue.",
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                  [InlineKeyboardButton(text="📋 All Products / Edit", callback_data="admin:product_edit")]]))
    await c.answer()


@dp.callback_query(F.data.startswith("admin:rename:"))
async def admin_rename_start(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    pid = c.data.rsplit(":", 1)[-1]
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT INTO user_input_state(telegram_id,action,product_id) VALUES(?,?,?) "
                         "ON CONFLICT(telegram_id) DO UPDATE SET action=excluded.action,product_id=excluded.product_id",
                         (c.from_user.id, "admin_rename_product", pid))
        await db.commit()
    await c.message.answer("Send the new display name (max 100 characters).")
    await c.answer()


@dp.callback_query(F.data.startswith("admin:hide:"))
async def admin_hide_product(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    pid = c.data.rsplit(":", 1)[-1]
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT INTO managed_products(product_id,enabled) VALUES(?,0) "
                         "ON CONFLICT(product_id) DO UPDATE SET enabled=0", (pid,))
        await db.commit()
    await c.message.answer("🙈 Product hidden from customer catalog. You can restore it with Add Product.")
    await c.answer()


@dp.callback_query(F.data == "admin:prices")
async def admin_prices(c: CallbackQuery):
    await show_admin_prices(c, 0)

@dp.callback_query(F.data.startswith("admin:prices_page:"))
async def admin_prices_page(c: CallbackQuery):
    try:
        page = int(c.data.rsplit(":", 1)[-1])
    except ValueError:
        page = 0
    await show_admin_prices(c, page)

@dp.callback_query(F.data == "admin:prices_search")
async def admin_prices_search_start(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO user_input_state(telegram_id,action) VALUES(?, 'admin_price_search') "
            "ON CONFLICT(telegram_id) DO UPDATE SET action='admin_price_search',"
            "order_id=NULL,product_id=NULL,created_at=CURRENT_TIMESTAMP",
            (c.from_user.id,))
        await db.commit()
    await c.message.answer("🔎 Send a product name to find it for price setup. Send /cancelsearch to exit.")
    await c.answer()


async def admin_price_search_results(m: Message, term: str):
    if m.from_user.id not in ADMIN_IDS:
        return
    if len(term) < 2:
        return await m.answer("Please enter at least 2 characters.")
    try:
        items = await catalog_products()
    except VenteBotError:
        return await m.answer("⚠️ Catalog is temporarily unavailable. Try again later.")
    matches = []
    for p in items:
        name = canonical_storebat_name(str(p.get("name") or p.get("title") or "")) or str(p.get("name") or p.get("title") or "")
        if term.casefold() in name.casefold():
            pid = str(p.get("id") or p.get("product_id") or p.get("uuid") or "")
            if pid:
                matches.append((p, pid, name))
    if not matches:
        return await m.answer("🔎 No matching products. Try another keyword.")
    rows = []
    for p, pid, name in matches[:15]:
        price = await selling_price_for_product(p)
        price_label = f"${price:.2f}" if isinstance(price, Decimal) else "Not set"
        rows.append([InlineKeyboardButton(
            text=f"📦 {name[:38]} | {price_label}"[:64],
            callback_data=f"admin:price_item:{pid}")])
    rows.append([InlineKeyboardButton(text="🔙 Product Prices", callback_data="admin:prices")])
    await m.answer(f"🔎 Price setup search: {term} ({len(matches)} found; showing up to 15)",
                   reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data == "admin:prices_noop")
async def admin_prices_noop(c: CallbackQuery):
    await c.answer()

@dp.callback_query(F.data.startswith("admin:price_item:"))
async def admin_price_item(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    pid = c.data.rsplit(":", 1)[-1]
    try:
        p = await get_product(pid)
    except VenteBotError as e:
        return await c.answer(f"Could not load product: {e}", show_alert=True)
    if not p:
        return await c.answer("Product not found", show_alert=True)
    name = str(p.get("name") or p.get("title") or "Product")
    selling = await selling_price_for_product(p)
    selling_text = f"${selling:.2f}" if isinstance(selling, Decimal) else "Not set"
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO user_input_state(telegram_id,action,product_id,product_name) VALUES(?,?,?,?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET action=excluded.action,order_id=NULL,product_id=excluded.product_id,"
            "unit_price_usd=NULL,product_name=excluded.product_name,created_at=CURRENT_TIMESTAMP",
            (c.from_user.id, "admin_set_price", pid, name),
        )
        await db.commit()
    await c.message.answer(
        f"💵 <b>{name}</b>\n"
        f"Current selling price: <b>{selling_text}</b>\n\n"
        "Send the new selling price in USD only.\n"
        "Example: <code>8.00</code>",
        parse_mode="HTML",
    )
    await c.answer()


def order_group(status):
    value = (status or "").lower().strip()
    if value in ("cancelled", "canceled", "failed", "payment_rejected", "rejected", "refunded"):
        return "cancelled"
    if value in ("delivered", "completed", "complete", "success", "fulfilled", "finished"):
        return "delivered"
    if value in ("awaiting_payment", "payment_submitted", "pending", "unpaid", "waiting", "pending_payment"):
        return "pending"
    return "processing"

@dp.callback_query(F.data.startswith("admin:all_orders:"))
async def admin_all_orders(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    parts = c.data.split(":")
    group = parts[2] if len(parts) == 4 else "all"
    if group not in ("all", "pending", "processing", "delivered", "cancelled"):
        return await c.answer("Invalid category", show_alert=True)
    try:
        page = max(0, int(parts[-1]))
    except ValueError:
        return await c.answer("Invalid page", show_alert=True)
    async with aiosqlite.connect(DB) as db:
        all_rows = await (await db.execute(
            "SELECT id,telegram_id,product_name,quantity,amount_usd,status,payment_method,payment_reference,created_at "
            "FROM orders ORDER BY id DESC")).fetchall()
    counts = {key: sum(order_group(r[5]) == key for r in all_rows) for key in ("pending", "processing", "delivered", "cancelled")}
    selected = [r for r in all_rows if group == "all" or order_group(r[5]) == group]
    per_page = 5
    pages = max(1, (len(selected) + per_page - 1) // per_page)
    page = min(page, pages - 1)
    rows = selected[page*per_page:(page+1)*per_page]
    label = {"all":"📦 All Orders", "pending":"🟡 Pending", "processing":"🔵 Processing", "delivered":"🟢 Delivered", "cancelled":"🔴 Cancelled"}[group]
    lines = [label, f"📊 Total: {len(selected)} | Page {page+1}/{pages}"]
    for oid, uid, product, qty, usd, status, method, ref, created in rows:
        raw_status = (status or "unknown").replace("_", " ").title()
        pay_status = ("Pending verification" if status == "payment_submitted" else
                      "Unpaid" if status == "awaiting_payment" else
                      "Rejected" if status == "payment_rejected" else
                      "Cancelled" if status == "cancelled" else
                      "Approved / supplier processing" if order_group(status) == "processing" else
                      "Approved / delivered" if order_group(status) == "delivered" else "Check order")
        lines.extend(["", "━━━━━━━━━━━━━━━━━━", f"🧾 Order ID: #{oid}",
                      f"📦 Product: {product or '—'}", f"🔢 Quantity: {qty or 1}",
                      f"👤 Customer ID: {uid}", f"💵 Amount: ${usd if usd is not None else '0'}",
                      f"💳 Method: {(method or 'binance').title()}",
                      f"💰 Payment Status: {pay_status}", f"📌 Order Status: {raw_status}",
                      f"📅 Order Date: {created or '—'}"])
    if not rows: lines.append("\nNo orders in this category.")
    keyboard = [
        [InlineKeyboardButton(text="📦 All", callback_data="admin:all_orders:all:0")],
        [InlineKeyboardButton(text=f"🟡 Pending ({counts['pending']})", callback_data="admin:all_orders:pending:0"),
         InlineKeyboardButton(text=f"🔵 Processing ({counts['processing']})", callback_data="admin:all_orders:processing:0")],
        [InlineKeyboardButton(text=f"🟢 Delivered ({counts['delivered']})", callback_data="admin:all_orders:delivered:0"),
         InlineKeyboardButton(text=f"🔴 Cancelled ({counts['cancelled']})", callback_data="admin:all_orders:cancelled:0")]
    ]
    nav=[]
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️ Previous", callback_data=f"admin:all_orders:{group}:{page-1}"))
    if page+1 < pages: nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"admin:all_orders:{group}:{page+1}"))
    if nav: keyboard.append(nav)
    keyboard.append([InlineKeyboardButton(text="🔙 Admin Menu", callback_data="admin:all_orders_back")])
    await c.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard))
    await c.answer()

@dp.callback_query(F.data == "admin:all_orders_back")
async def admin_all_orders_back(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    await c.message.edit_text("⚙️ Ismail Digistore Admin", reply_markup=ADMIN_KB)
    await c.answer()

@dp.callback_query(F.data == "admin:pending")
async def admin_pending(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return await c.answer("Not authorized", show_alert=True)
    async with aiosqlite.connect(DB) as db:
        rows = await (await db.execute("SELECT id,telegram_id,product_name,amount_usd,payment_reference,activation_identifier,payment_method FROM orders WHERE status='payment_submitted' ORDER BY id LIMIT 20")).fetchall()
    if not rows:
        await c.message.answer("✅ No payments are waiting for approval.")
    else:
        for oid,uid,name,amount,ref,act,method in rows:
            keys=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"✅ Approve #{oid}", callback_data=f"admin:approve:{oid}"), InlineKeyboardButton(text=f"❌ Reject #{oid}", callback_data=f"admin:reject:{oid}")]])
            await c.message.answer(f"🧾 #{oid} • {name or 'Product'}\\n👤 {uid}\\n💵 ${amount or '—'}\\n💳 {method or 'binance'} Ref: {ref or '—'}\\n🔑 ID: {act or 'Not supplied'}", reply_markup=keys)
    await c.answer()

async def submit_supplier_order(oid: int):
    async with aiosqlite.connect(DB) as db:
        row = await (await db.execute("SELECT telegram_id,product_id,quantity,idempotency_key,status,activation_identifier FROM orders WHERE id=?", (oid,))).fetchone()
    if not row or row[4] != "payment_submitted":
        raise VenteBotError("Order not found or payment is not awaiting approval.")
    uid,pid,qty,idem,_,activation=row
    await vente.quote(pid, qty, activation)
    result=await vente.create_order(pid, qty, activation, f"digistore-{oid}-user-{uid}", idem)
    data=(result.get("data") or {}) if isinstance(result,dict) else {}
    supplier_id=str(result.get("id") or result.get("order_id") or data.get("id") or data.get("order_id") or "") if isinstance(result,dict) else ""
    status=(result.get("status") or data.get("status") or "submitted") if isinstance(result,dict) else "submitted"
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE orders SET supplier_order_id=?, status=? WHERE id=?", (supplier_id,status,oid)); await db.commit()
    return uid,supplier_id,status

async def send_delivery_if_ready(bot: Bot, oid: int, uid: int, supplier_id: str):
    if not supplier_id: return False
    # Short polling window for instant-delivery products. Non-instant orders remain processing.
    for delay in (0, 2, 4, 6):
        if delay: await asyncio.sleep(delay)
        try: result = await vente.order(supplier_id)
        except VenteBotError: continue
        data=(result.get("data") or {}) if isinstance(result,dict) else {}
        status=(result.get("status") or data.get("status") or "processing") if isinstance(result,dict) else "processing"
        delivery=(result.get("delivery") or result.get("credentials") or result.get("account") or data.get("delivery") or data.get("credentials") or data.get("account")) if isinstance(result,dict) else None
        async with aiosqlite.connect(DB) as db:
            previous=(await (await db.execute("SELECT status FROM orders WHERE id=?",(oid,))).fetchone())
            await db.execute("UPDATE orders SET status=? WHERE id=?",(str(status),oid)); await db.commit()
        if previous and str(previous[0]).lower()!=str(status).lower():
            try: await bot.send_message(uid,f"📦 Order #{oid} updated: {customer_status_label(status)}")
            except Exception: pass
        if delivery:
            await bot.send_message(uid, f"🎉 <b>Order #{oid} Delivered</b>\n\n{delivery}", parse_mode="HTML")
            return True
    return False

@dp.callback_query(F.data.startswith("admin:approve:"))
async def admin_approve_button(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return await c.answer("Not authorized", show_alert=True)
    oid=int(c.data.rsplit(":",1)[1])
    try: uid,sid,status=await submit_supplier_order(oid)
    except VenteBotError as e: await c.message.answer(f"⚠️ Supplier order was NOT created: {e}"); return await c.answer()
    await c.message.answer(f"✅ Payment approved. Order #{oid} sent to supplier. Supplier ID: {sid or 'not returned'}")
    try:
        await c.bot.send_message(uid, f"✅ Payment approved for Order #{oid}. Your product is now being processed.")
        delivered = await send_delivery_if_ready(c.bot, oid, uid, sid)
        if not delivered: await c.bot.send_message(uid, "⏳ Supplier is still processing this order. Delivery will appear when available.")
    except Exception: pass
    await c.answer()

@dp.callback_query(F.data.startswith("admin:reject:"))
async def admin_reject_button(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return await c.answer("Not authorized", show_alert=True)
    oid=int(c.data.rsplit(":",1)[1])
    async with aiosqlite.connect(DB) as db:
        row=await (await db.execute("SELECT telegram_id FROM orders WHERE id=? AND status='payment_submitted'",(oid,))).fetchone()
        if row: await db.execute("UPDATE orders SET status='payment_rejected' WHERE id=?",(oid,)); await db.commit()
    if not row: await c.message.answer("Order is no longer pending."); return await c.answer()
    await c.message.answer(f"❌ Payment for Order #{oid} rejected. No supplier order was created.")
    try: await c.bot.send_message(row[0], f"❌ Payment for Order #{oid} could not be verified. Please contact support.")
    except Exception: pass
    await c.answer()

@dp.message(Command("status"))
async def refresh_status(m: Message):
    parts=(m.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit(): return await m.answer("Use: /status ORDER_ID")
    oid=int(parts[1])
    async with aiosqlite.connect(DB) as db:
        row=await (await db.execute("SELECT telegram_id,supplier_order_id,status FROM orders WHERE id=?",(oid,))).fetchone()
    if not row or (m.from_user.id!=row[0] and m.from_user.id not in ADMIN_IDS): return await m.answer("Order not found.")
    if not row[1]: return await m.answer(f"Order #{oid} status: {row[2]}")
    try: result=await vente.order(row[1])
    except VenteBotError as e: return await m.answer(f"⚠️ Could not refresh status: {e}")
    data=(result.get("data") or {}) if isinstance(result,dict) else {}
    status=(result.get("status") or data.get("status") or row[2]) if isinstance(result,dict) else row[2]
    delivery=(result.get("delivery") or result.get("credentials") or data.get("delivery") or data.get("credentials")) if isinstance(result,dict) else None
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE orders SET status=? WHERE id=?",(str(status),oid)); await db.commit()
    if str(status).lower()!=str(row[2]).lower():
        try: await m.bot.send_message(row[0],f"📦 Order #{oid} updated: {customer_status_label(status)}")
        except Exception: pass
    text=f"📦 Order #{oid}\\nStatus: {status}"
    if delivery: text += f"\\n\\n🎁 Delivery:\\n{delivery}"
    await m.answer(text)

@dp.callback_query(F.data == "admin:status_help")
async def admin_status_help(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return await c.answer("Not authorized", show_alert=True)
    await c.message.answer("Use /status ORDER_ID to refresh an order directly from VenteBot.")
    await c.answer()

@dp.message(Command("paid"))
async def paid(m: Message):
    parts = (m.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        return await m.answer("Use: /paid ORDER_ID PAYMENT_REFERENCE")
    oid, ref = int(parts[1]), parts[2].strip()
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("UPDATE orders SET payment_reference=?, status='payment_submitted' WHERE id=? AND telegram_id=? AND status='awaiting_payment'", (ref, oid, m.from_user.id))
        await db.commit()
    await m.answer("✅ Payment reference submitted. Admin will verify it before the supplier order is created." if cur.rowcount else "⚠️ Order not found or payment was already submitted.")

@dp.message(Command("setpayment"))
async def setpayment(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    text=(m.text or "").split(maxsplit=1)
    if len(text)<2: return await m.answer("Admin usage: /setpayment PAYMENT_INSTRUCTIONS")
    await set_setting_value("payment_instructions", text[1].strip())
    await m.answer("✅ Payment instructions updated.")

@dp.message(Command("setreferral"))
async def setreferral(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    parts=(m.text or "").split(maxsplit=5)
    if len(parts)<5:
        return await m.answer("Usage: /setreferral PRODUCT_ID REQUIRED free PRODUCT_NAME\nor /setreferral PRODUCT_ID REQUIRED discount USD_PRICE PRODUCT_NAME")
    pid=parts[1]
    try: required=int(parts[2]); assert required>0
    except: return await m.answer("Required referrals must be a positive number.")
    rtype=parts[3].lower()
    if rtype=="free":
        special=None; name=" ".join(parts[4:]).strip()
    elif rtype=="discount":
        if len(parts)<6: return await m.answer("Discount usage: /setreferral PRODUCT_ID REQUIRED discount USD_PRICE PRODUCT_NAME")
        try: special=Decimal(parts[4]); assert special>=0
        except: return await m.answer("Invalid USD price.")
        name=parts[5].strip()
    else: return await m.answer("Reward type must be free or discount.")
    async with aiosqlite.connect(DB) as db:
        cur=await db.execute("INSERT INTO referral_campaigns(product_id,product_name,referrals_required,reward_type,special_price_usd,enabled) VALUES(?,?,?,?,?,1)", (pid,name,required,rtype,None if special is None else f"{special:.2f}"))
        cid=cur.lastrowid; await db.commit()
    await m.answer(f"✅ Referral campaign #{cid} created for {name}.")

@dp.message(Command("refdisable"))
async def refdisable(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    parts=(m.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit(): return await m.answer("Usage: /refdisable CAMPAIGN_ID")
    async with aiosqlite.connect(DB) as db:
        cur=await db.execute("UPDATE referral_campaigns SET enabled=0 WHERE id=?", (int(parts[1]),)); await db.commit()
    await m.answer("✅ Campaign disabled." if cur.rowcount else "Campaign not found.")

@dp.callback_query(F.data == "admin:ref_help")
async def admin_ref_help(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return await c.answer("Not authorized", show_alert=True)
    await c.message.answer("🎁 Referral admin commands:\n/setreferral PRODUCT_ID REQUIRED free PRODUCT_NAME\n/setreferral PRODUCT_ID REQUIRED discount USD_PRICE PRODUCT_NAME\n/refdisable CAMPAIGN_ID")
    await c.answer()

@dp.callback_query(F.data == "admin:payment_help")
async def admin_payment_help(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return await c.answer("Not authorized", show_alert=True)
    current=await get_setting("payment_instructions", "Not configured")
    await c.message.answer(f"💳 Current payment instructions:\n{current}\n\nChange with:\n/setpayment YOUR_PAYMENT_INSTRUCTIONS")
    await c.answer()

@dp.message(Command("setprice"))
async def setprice(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    parts = (m.text or "").split()
    if len(parts) != 3: return await m.answer("Admin usage: /setprice PRODUCT_ID USD_PRICE")
    try:
        val = Decimal(parts[2]); assert val >= 0
    except (InvalidOperation, AssertionError): return await m.answer("Invalid USD price.")
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT INTO prices(product_id,selling_price_usd) VALUES(?,?) ON CONFLICT(product_id) DO UPDATE SET selling_price_usd=excluded.selling_price_usd", (parts[1], f"{val:.2f}")); await db.commit()
    await m.answer(f"✅ Selling price for {parts[1]} = ${val:.2f}")

@dp.message(Command("approve"))
async def approve(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    parts=(m.text or "").split(maxsplit=2)
    if len(parts)<2 or not parts[1].isdigit(): return await m.answer("Admin usage: /approve ORDER_ID [ACTIVATION_IDENTIFIER]")
    oid=int(parts[1])
    if len(parts)>2:
        async with aiosqlite.connect(DB) as db:
            await db.execute("UPDATE orders SET activation_identifier=? WHERE id=?",(parts[2].strip(),oid)); await db.commit()
    try: uid,sid,status=await submit_supplier_order(oid)
    except VenteBotError as e: return await m.answer(f"⚠️ Supplier order was NOT created: {e}")
    await m.answer(f"✅ Order #{oid} submitted to supplier. Supplier ID: {sid or 'returned without ID'}")
    try: await m.bot.send_message(uid, f"✅ Payment approved. Order #{oid} has been submitted for delivery. Status: {status}")
    except Exception: pass

@dp.message(Command("setoffer"))
async def setoffer(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    parts=(m.text or "").split(maxsplit=3)
    if len(parts)<4: return await m.answer("Usage: /setoffer PRODUCT_ID USD_PRICE PRODUCT_NAME")
    try: price=Decimal(parts[2]); assert price>=0
    except: return await m.answer("Invalid USD price.")
    async with aiosqlite.connect(DB) as db:
        cur=await db.execute("INSERT INTO offers(product_id,product_name,offer_price_usd,enabled) VALUES(?,?,?,1)",(parts[1],parts[3],f"{price:.2f}")); await db.commit()
    await m.answer(f"✅ Offer #{cur.lastrowid} created: {parts[3]} — ${price:.2f}")

@dp.message(Command("offerdisable"))
async def offerdisable(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    parts=(m.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit(): return await m.answer("Usage: /offerdisable OFFER_ID")
    async with aiosqlite.connect(DB) as db:
        cur=await db.execute("UPDATE offers SET enabled=0 WHERE id=?",(int(parts[1]),)); await db.commit()
    await m.answer("✅ Offer disabled." if cur.rowcount else "Offer not found.")

@dp.message(F.text == "🔥 Offers")
async def offers(m: Message):
    async with aiosqlite.connect(DB) as db:
        rows=await (await db.execute("""SELECT id,product_id,product_name,offer_price_usd FROM offers WHERE enabled=1 AND (starts_at IS NULL OR starts_at<=CURRENT_TIMESTAMP) AND (ends_at IS NULL OR ends_at>=CURRENT_TIMESTAMP) ORDER BY id DESC LIMIT 20""")).fetchall()
    if not rows: return await m.answer("🔥 No active offers right now.")
    for oid,pid,name,price in rows:
        markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🛒 Buy ${Decimal(price):.2f}",callback_data=f"offerbuy:{oid}")]])
        await m.answer(f"🔥 {name or pid}\n🏷 Offer price: ${Decimal(price):.2f}",reply_markup=markup)

@dp.callback_query(F.data.startswith("offerbuy:"))
async def offer_buy(c: CallbackQuery):
    offer_id=int(c.data.split(":",1)[1])
    async with aiosqlite.connect(DB) as db:
        row=await (await db.execute("""SELECT product_id,product_name,offer_price_usd FROM offers WHERE id=? AND enabled=1 AND (starts_at IS NULL OR starts_at<=CURRENT_TIMESTAMP) AND (ends_at IS NULL OR ends_at>=CURRENT_TIMESTAMP)""",(offer_id,))).fetchone()
        if not row: return await c.answer("Offer is no longer available.",show_alert=True)
        pid,name,price=row; idem=str(uuid.uuid4())
        cur=await db.execute("INSERT INTO orders(telegram_id,product_id,product_name,quantity,amount_usd,idempotency_key,status) VALUES(?,?,?,?,?,?,?)",(c.from_user.id,pid,name or pid,1,price,idem,"awaiting_payment")); await db.commit(); order_id=cur.lastrowid
    await c.message.answer(f"🔥 Offer order #{order_id} created. Total: ${Decimal(price):.2f}\nIf required: /activate {order_id} YOUR_IDENTIFIER\nAfter payment: /paid {order_id} YOUR_PAYMENT_REFERENCE")
    await c.answer()

@dp.message(F.text == "🎁 Referral Offers")
async def referrals(m: Message):
    count = await referral_count(m.from_user.id)
    me = await m.bot.get_me()
    await m.answer(f"🎁 Your valid referrals: {count}\n🔗 Your link: https://t.me/{me.username}?start=ref_{m.from_user.id}")
    async with aiosqlite.connect(DB) as db:
        rows = await (await db.execute("""SELECT id,product_id,product_name,referrals_required,reward_type,special_price_usd
            FROM referral_campaigns WHERE enabled=1
            AND (starts_at IS NULL OR starts_at<=CURRENT_TIMESTAMP)
            AND (ends_at IS NULL OR ends_at>=CURRENT_TIMESTAMP) ORDER BY id DESC""")).fetchall()
        redeemed = {r[0] for r in await (await db.execute("SELECT campaign_id FROM referral_redemptions WHERE telegram_id=?", (m.from_user.id,))).fetchall()}
    if not rows:
        return await m.answer("No referral reward campaign is active right now. Your referral count is saved for future offers.")
    for cid,pid,name,need,rtype,special in rows:
        reward = "FREE" if rtype == "free" else f"${Decimal(special):.2f}"
        state = "✅ Eligible" if count >= need else f"🔒 Need {need-count} more"
        if cid in redeemed: state = "☑️ Already redeemed"
        markup = None
        if count >= need and cid not in redeemed:
            markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎁 Redeem", callback_data=f"refredeem:{cid}")]])
        await m.answer(f"🎁 {name or pid}\n👥 Required referrals: {need}\n🏷 Reward price: {reward}\n{state}", reply_markup=markup)

@dp.callback_query(F.data.startswith("refredeem:"))
async def redeem_referral(c: CallbackQuery):
    cid = int(c.data.split(":",1)[1])
    count = await referral_count(c.from_user.id)
    async with aiosqlite.connect(DB) as db:
        row = await (await db.execute("""SELECT product_id,product_name,referrals_required,reward_type,special_price_usd
            FROM referral_campaigns WHERE id=? AND enabled=1
            AND (starts_at IS NULL OR starts_at<=CURRENT_TIMESTAMP)
            AND (ends_at IS NULL OR ends_at>=CURRENT_TIMESTAMP)""", (cid,))).fetchone()
        exists = await (await db.execute("SELECT 1 FROM referral_redemptions WHERE campaign_id=? AND telegram_id=?", (cid,c.from_user.id))).fetchone()
        if not row or exists:
            await c.answer("Offer unavailable or already redeemed.", show_alert=True); return
        pid,name,need,rtype,special = row
        if count < need:
            await c.answer("You do not have enough valid referrals yet.", show_alert=True); return
        amount = Decimal("0.00") if rtype == "free" else Decimal(special)
        status = "payment_submitted" if rtype == "free" else "awaiting_payment"
        idem = str(uuid.uuid4())
        cur = await db.execute("INSERT INTO orders(telegram_id,product_id,product_name,quantity,amount_usd,idempotency_key,status,payment_reference) VALUES(?,?,?,?,?,?,?,?)",
            (c.from_user.id,pid,name or pid,1,f"{amount:.2f}",idem,status,f"REFERRAL_REWARD_CAMPAIGN_{cid}" if rtype=="free" else None))
        oid=cur.lastrowid
        await db.execute("INSERT INTO referral_redemptions(campaign_id,telegram_id,order_id) VALUES(?,?,?)", (cid,c.from_user.id,oid))
        await db.commit()
    if rtype == "free":
        await c.message.answer(f"🎁 Free reward claimed as Order #{oid}. Admin approval is required before the supplier wallet is charged. Add an activation ID with /activate {oid} YOUR_IDENTIFIER if required.")
    else:
        await c.message.answer(f"🎁 Referral price unlocked! Order #{oid} total: ${amount:.2f}. If needed use /activate {oid} YOUR_IDENTIFIER, then pay and submit /paid {oid} YOUR_PAYMENT_REFERENCE")
    await c.answer("Reward claimed")

@dp.message(F.text == "📦 My Orders")
async def my_orders(m: Message):
    async with aiosqlite.connect(DB) as db:
        rows = await (await db.execute("SELECT id,product_name,amount_usd,status,created_at FROM orders WHERE telegram_id=? ORDER BY id DESC LIMIT 10", (m.from_user.id,))).fetchall()
    if not rows: return await m.answer("📦 You have no orders yet.")
    text = ["📦 Your latest orders:"] + [f"#{o} • {n or 'Product'} • ${a or '—'} • {s} • {d}" for o,n,a,s,d in rows]
    await m.answer("\n".join(text))

def payment_category_keyboard():
    # Telegram inline buttons cannot contain image logos; branded emoji labels are used.
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌍 International Payment · 🟡 Binance Pay", callback_data="payment_info:binance")],
        [InlineKeyboardButton(text="🇧🇩 Local Payment · 💗 bKash", callback_data="payment_info:bkash")],
        [InlineKeyboardButton(text="🇧🇩 Local Payment · 🟠 Nagad", callback_data="payment_info:nagad")],
    ])

@dp.message(F.text == "💳 Payment")
async def payment(m: Message):
    await m.answer("💳 Select Payment Method:\n\n🌍 International Payment — Binance Pay\n🇧🇩 Local Payment — bKash / Nagad", reply_markup=payment_category_keyboard())

@dp.callback_query(F.data.startswith("payment_category:"))
async def payment_category(c: CallbackQuery):
    # Backward compatibility with buttons sent by older versions of the bot.
    category = c.data.split(":", 1)[1]
    if category == "international":
        await c.message.edit_text("🌍 International Payment", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟡 Binance Pay", callback_data="payment_info:binance")],
            [InlineKeyboardButton(text="⬅️ Back", callback_data="payment_info:back")]]))
    elif category == "local":
        await c.message.edit_text("🇧🇩 Local Payment", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💗 bKash", callback_data="payment_info:bkash"), InlineKeyboardButton(text="🟠 Nagad", callback_data="payment_info:nagad")],
            [InlineKeyboardButton(text="⬅️ Back", callback_data="payment_info:back")]]))
    else:
        return await c.answer("Invalid payment category", show_alert=True)
    await c.answer()

@dp.callback_query(F.data.startswith("payment_info:"))
async def payment_info(c: CallbackQuery):
    method = c.data.split(":", 1)[1]
    if method == "back":
        text = "💳 Select Payment Method:\n\n🌍 International Payment — Binance Pay\n🇧🇩 Local Payment — bKash / Nagad"
        buttons = payment_category_keyboard()
    elif method == "binance":
        instructions = await get_setting("payment_instructions", "Payment method has not been configured yet. Please contact support.")
        text = f"🌍 International Payment — 🟡 Binance Pay\n\n{instructions}"
        buttons = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="payment_info:back")]])
    elif method in ("bkash", "nagad"):
        name, number, logo = ("bKash", BKASH_NUMBER, "💗") if method == "bkash" else ("Nagad", NAGAD_NUMBER, "🟠")
        text = (f"🇧🇩 Local Payment — {logo} {name}\n"
                f"📱 Personal Send Money: {number}\n"
                "💱 Conversion: 1 USD = ৳131\n\n"
                "To see the exact amount, select a product and place an order. "
                "Choose this payment method at checkout, send the amount shown, "
                "and submit the Transaction ID for admin verification.")
        buttons = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="payment_info:back")]])
    else:
        return await c.answer("Invalid payment method", show_alert=True)
    # Edit the original menu message instead of adding a new one on every click.
    await c.message.edit_text(text, reply_markup=buttons)
    await c.answer()

@dp.message(Command("setsupport"))
async def setsupport(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    parts=(m.text or "").split(maxsplit=1)
    if len(parts)<2: return await m.answer("Usage: /setsupport YOUR_SUPPORT_TEXT_OR_USERNAME")
    await set_setting_value("support_contact",parts[1].strip()); await m.answer("✅ Support information updated.")

@dp.message(F.text == "💬 Support")
async def support(m: Message):
    await m.answer("💬 Contact our support team:", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="💬 Contact Support", url="https://t.me/Ismail0987651")]]))

@dp.message(Command("broadcast"))
async def broadcast(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    parts=(m.text or "").split(maxsplit=1)
    if len(parts)<2: return await m.answer("Usage: /broadcast MESSAGE")
    async with aiosqlite.connect(DB) as db: users=await (await db.execute("SELECT telegram_id FROM users")).fetchall()
    sent=failed=0
    for (uid,) in users:
        try: await m.bot.send_message(uid,parts[1]); sent+=1
        except Exception: failed+=1
        await asyncio.sleep(0.04)
    await m.answer(f"📢 Broadcast complete. Sent: {sent} | Failed: {failed}")

@dp.callback_query(F.data == "admin:offer_help")
async def admin_offer_help(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return await c.answer("Not authorized",show_alert=True)
    await c.message.answer("🔥 Offer commands:\n/setoffer PRODUCT_ID USD_PRICE PRODUCT_NAME\n/offerdisable OFFER_ID"); await c.answer()

@dp.callback_query(F.data == "admin:support_help")
async def admin_support_help(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return await c.answer("Not authorized",show_alert=True)
    current=await get_setting("support_contact","Not configured")
    await c.message.answer(f"💬 Current support: {current}\n\nChange with: /setsupport YOUR_SUPPORT_TEXT"); await c.answer()

@dp.callback_query(F.data == "admin:broadcast_help")
async def admin_broadcast_help(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return await c.answer("Not authorized",show_alert=True)
    await c.message.answer("📢 Send to all users with:\n/broadcast YOUR_MESSAGE"); await c.answer()

@dp.callback_query(F.data == "admin:stats")
async def admin_stats(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return await c.answer("Not authorized",show_alert=True)
    async with aiosqlite.connect(DB) as db:
        users=(await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        orders=(await (await db.execute("SELECT COUNT(*) FROM orders")).fetchone())[0]
        pending=(await (await db.execute("SELECT COUNT(*) FROM orders WHERE status='payment_submitted'")).fetchone())[0]
        sales=(await (await db.execute("SELECT COALESCE(SUM(CAST(amount_usd AS REAL)),0) FROM orders WHERE status NOT IN ('awaiting_payment','payment_submitted','cancelled','failed')")).fetchone())[0]
        refs=(await (await db.execute("SELECT COUNT(*) FROM users WHERE referrer_id IS NOT NULL")).fetchone())[0]
    await c.message.answer(f"📊 Store Statistics\n👥 Users: {users}\n📦 Orders: {orders}\n⏳ Pending approvals: {pending}\n🎁 Referred users: {refs}\n💵 Processed order value: ${sales:.2f}"); await c.answer()

# Additional customer tools. Existing payment/order handlers remain in place.
def customer_status_label(status):
    status = str(status or "unknown").lower()
    if status in ("awaiting_payment", "payment_submitted", "pending", "payment_pending"):
        return "🟡 Pending" + (" — payment verification" if status == "payment_submitted" else "")
    if status in ("processing", "submitted", "approved", "in_progress", "in progress"):
        return "🔵 Processing"
    if status in ("delivered", "completed", "complete", "success", "fulfilled"):
        return "🟢 Delivered"
    if status in ("cancelled", "canceled", "failed", "payment_rejected", "rejected"):
        return "🔴 Cancelled / Rejected"
    return "📌 " + status.replace("_", " ").title()

async def show_customer_order(m, oid):
    async with aiosqlite.connect(DB) as db:
        row = await (await db.execute(
            "SELECT id,product_name,quantity,amount_usd,status,payment_method,created_at FROM orders WHERE id=? AND telegram_id=?",
            (oid, m.from_user.id))).fetchone()
    if not row:
        return await m.answer("⚠️ Order not found in your account.")
    number,name,qty,amount,status,method,created = row
    await m.answer(f"📦 Order #{number}\n🛍️ {name or 'Product'}\n🔢 Quantity: {qty}\n"
                   f"💵 Total: ${amount or '—'}\n💳 Method: {method or '—'}\n"
                   f"📍 {customer_status_label(status)}\n🕒 Created: {created}\n\n"
                   f"🔄 To refresh supplier status, send /status {number}.")

@dp.message(F.text == "🔎 Search Products")
async def customer_search_start(m: Message):
    async with aiosqlite.connect(DB) as db:
        await db.execute("INSERT INTO user_input_state(telegram_id,action) VALUES(?, 'product_search') "
                         "ON CONFLICT(telegram_id) DO UPDATE SET action='product_search',order_id=NULL,product_id=NULL",
                         (m.from_user.id,))
        await db.commit()
    await m.answer("🔎 Type a product name to search. Send /cancelsearch to exit.")

@dp.message(Command("cancelsearch"))
async def customer_search_cancel(m: Message):
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM user_input_state WHERE telegram_id=? AND action='product_search'",(m.from_user.id,))
        await db.commit()
    await m.answer("Search closed.")

@dp.message(Command("search"))
async def customer_search_command(m: Message):
    term=(m.text or "").partition(" ")[2].strip()
    if not term: return await m.answer("Use /search PRODUCT_NAME")
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM user_input_state WHERE telegram_id=? AND action LIKE 'admin_%'",(m.from_user.id,))
        await db.commit()
    await customer_search_results(m,term)

async def customer_search_results(m, term):
    if len(term) < 2: return await m.answer("Enter at least 2 characters.")
    try: items=await catalog_products()
    except VenteBotError: return await m.answer("⚠️ Catalog is temporarily unavailable. Try again later.")
    matches=[]
    for product in items:
        name=str(product.get("name") or product.get("title") or "")
        if name and term.casefold() in name.casefold(): matches.append((product,name))
    if not matches: return await m.answer("🔎 No matching products found. Try another keyword.")
    buttons=[]
    for product,name in matches[:15]:
        pid=str(product.get("id") or product.get("product_id") or product.get("uuid") or "")
        if not pid: continue
        price=await display_price(product)
        label=f"📦 {name[:34]} — ${price:.2f}" if isinstance(price,Decimal) else f"📦 {name[:40]}"
        buttons.append([InlineKeyboardButton(text=label[:64],callback_data=f"product:{pid}")])
    if not buttons: return await m.answer("No available matching products.")
    await m.answer(f"🔎 Results for: {term} ({len(matches)} found; showing up to 15)",
                   reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data == "admin:sales_dashboard")
async def admin_sales_dashboard(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return await c.answer("Not authorized",show_alert=True)
    async with aiosqlite.connect(DB) as db:
        today=(await (await db.execute("SELECT COUNT(*) FROM orders WHERE date(created_at)=date('now')")).fetchone())[0]
        total=(await (await db.execute("SELECT COUNT(*) FROM orders")).fetchone())[0]
        pending=(await (await db.execute("SELECT COUNT(*) FROM orders WHERE lower(status) IN ('awaiting_payment','payment_submitted','pending')")).fetchone())[0]
        # Only supplier-submitted/fulfilled orders count as processed order value; not profit.
        sales=(await (await db.execute("SELECT COALESCE(SUM(CAST(amount_usd AS REAL)),0) FROM orders WHERE lower(status) IN ('submitted','processing','delivered','completed','complete','success','fulfilled')")).fetchone())[0]
        leaders=await (await db.execute("SELECT COALESCE(product_name,'Product'),COUNT(*) AS n FROM orders "
                         "WHERE lower(status) IN ('submitted','processing','delivered','completed','complete','success','fulfilled') "
                         "GROUP BY product_id ORDER BY n DESC LIMIT 5")).fetchall()
    ranking="\n".join(f"{i}. {name[:40]} — {n}" for i,(name,n) in enumerate(leaders,1)) or "No processed orders yet"
    await c.message.answer(f"📈 Sales Dashboard (UTC)\n\n📦 Today's orders: {today}\n"
                           f"🧾 All orders: {total}\n🟡 Pending: {pending}\n"
                           f"💵 Processed order value: ${sales:.2f}\n\n🏆 Top products (processed orders):\n{ranking}")
    await c.answer()

@dp.message(Command("cancelprice"))
async def cancel_price_command(m: Message):
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM user_input_state WHERE telegram_id=? AND action IN ('admin_set_price','admin_confirm_price')",(m.from_user.id,))
        await db.commit()
    await m.answer("❌ Price edit cancelled. No price was changed.")


@dp.callback_query(F.data.in_({"admin:price_confirm", "admin:price_cancel"}))
async def admin_price_confirm_or_cancel(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized",show_alert=True)
    async with aiosqlite.connect(DB) as db:
        state=await (await db.execute("SELECT product_id,unit_price_usd FROM user_input_state WHERE telegram_id=? AND action='admin_confirm_price'",(c.from_user.id,))).fetchone()
        if not state:
            return await c.answer("No pending price edit",show_alert=True)
        pid, amount=state
        if c.data == "admin:price_confirm":
            await db.execute("INSERT INTO prices(product_id,selling_price_usd) VALUES(?,?) ON CONFLICT(product_id) DO UPDATE SET selling_price_usd=excluded.selling_price_usd",(pid,amount))
        await db.execute("DELETE FROM user_input_state WHERE telegram_id=? AND action='admin_confirm_price'",(c.from_user.id,))
        await db.commit()
    await c.message.edit_text((f"✅ Confirmed: product ID {pid} price changed to ${amount}." if c.data == "admin:price_confirm" else "❌ Price edit cancelled. No changes made."))
    await c.answer()


@dp.message(F.text)
async def pending_text_input(m: Message):
    # Handles only text explicitly requested by a previous button (custom quantity / payment reference).
    async with aiosqlite.connect(DB) as db:
        state=await (await db.execute("SELECT action,order_id,product_id FROM user_input_state WHERE telegram_id=?",(m.from_user.id,))).fetchone()
    if not state: return
    action, oid, pid = state
    text=(m.text or "").strip()
    if action == "admin_add_search":
        return await admin_manage_search(m, text, "add")
    if action == "admin_edit_search":
        return await admin_manage_search(m, text, "edit")
    if action == "admin_rename_product":
        if m.from_user.id not in ADMIN_IDS: return
        if not 1 <= len(text) <= 100: return await m.answer("Name must be 1–100 characters.")
        async with aiosqlite.connect(DB) as db:
            await db.execute("INSERT INTO managed_products(product_id,display_name,enabled) VALUES(?,?,1) "
                             "ON CONFLICT(product_id) DO UPDATE SET display_name=excluded.display_name",
                             (pid, text))
            await db.execute("DELETE FROM user_input_state WHERE telegram_id=?", (m.from_user.id,))
            await db.commit()
        return await m.answer("✅ Product display name updated.")
    if action == "product_search":
        return await customer_search_results(m,text)
    if action == "admin_price_search":
        return await admin_price_search_results(m,text)
    if action == "admin_set_price":
        if m.from_user.id not in ADMIN_IDS: return
        try:
            val = Decimal(text)
            if not val.is_finite() or val < 0: raise InvalidOperation
        except (InvalidOperation, ValueError):
            return await m.answer("⚠️ Enter a valid USD price, or /cancelprice to stop.")
        try:
            product = await get_product(pid)
            name = str(product.get("name") or product.get("title") or pid) if product else pid
            old = await selling_price_for_product(product) if product else None
        except VenteBotError:
            return await m.answer("⚠️ Supplier unavailable; no price changed.")
        async with aiosqlite.connect(DB) as db:
            await db.execute("UPDATE user_input_state SET action='admin_confirm_price',unit_price_usd=? WHERE telegram_id=? AND action='admin_set_price' AND product_id=?", (f"{val:.2f}",m.from_user.id,pid))
            await db.commit()
        old_text = f"${old:.2f}" if isinstance(old,Decimal) else "Not set"
        await m.answer(f"⚠️ Confirm price change\n📦 Product: {name}\n💵 Old price: {old_text}\n💵 New price: ${val:.2f}\n\nNo change until you confirm.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Confirm price change",callback_data="admin:price_confirm")],
                [InlineKeyboardButton(text="❌ Cancel",callback_data="admin:price_cancel")]]))
        return
    if action == "admin_confirm_price":
        return await m.answer("⚠️ Price change is awaiting confirmation. Use the Confirm or Cancel button above.")
    if action == "custom_qty":
        if not text.isdigit() or not (1 <= int(text) <= 100):
            return await m.answer("⚠️ Send a quantity from 1 to 100, for example: 8")
        # Clear state first, then create the order through the same safe path.
        async with aiosqlite.connect(DB) as db:
            await db.execute("DELETE FROM user_input_state WHERE telegram_id=?",(m.from_user.id,)); await db.commit()
        class MsgCallback:
            from_user=m.from_user; message=m
        await create_customer_order(MsgCallback(), pid, int(text))
        return
    if action == "payment_ref":
        if len(text) < 4 or len(text) > 200:
            return await m.answer("⚠️ Please send a valid payment Transaction ID / Order ID.")
        async with aiosqlite.connect(DB) as db:
            duplicate=await (await db.execute("SELECT id FROM orders WHERE payment_reference=? AND id<>?",(text,oid))).fetchone()
            if duplicate: return await m.answer("⚠️ This transaction reference has already been used. Please check it and send the correct one.")
            cur=await db.execute("UPDATE orders SET payment_reference=?, status='payment_submitted' WHERE id=? AND telegram_id=? AND status='awaiting_payment'",(text,oid,m.from_user.id))
            if cur.rowcount: await db.execute("DELETE FROM user_input_state WHERE telegram_id=?",(m.from_user.id,))
            await db.commit()
        if not cur.rowcount: return await m.answer("⚠️ This order is no longer awaiting payment.")
        await m.answer(f"✅ Transaction reference received for Order #{oid}.\n\n⏳ Payment is waiting for admin verification. The supplier will NOT be charged until you are approved.")
        # Push the pending payment directly to every admin.
        async with aiosqlite.connect(DB) as db:
            row=await (await db.execute("SELECT product_name,amount_usd,activation_identifier,payment_method FROM orders WHERE id=?",(oid,))).fetchone()
        name,amount,act,method=row if row else ("Product","—",None,"binance")
        keys=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"✅ Approve #{oid}",callback_data=f"admin:approve:{oid}"),InlineKeyboardButton(text=f"❌ Reject #{oid}",callback_data=f"admin:reject:{oid}")]])
        for admin_id in ADMIN_IDS:
            try: await m.bot.send_message(admin_id,f"💳 <b>Payment submitted</b>\n🧾 Order #{oid}\n📦 {name}\n👤 {m.from_user.id}\n💵 ${amount}\n🔎 Ref: <code>{text}</code>\n🔑 ID: {act or 'Not supplied'}",parse_mode="HTML",reply_markup=keys)
            except Exception: pass

async def main():
    if not TOKEN or "PASTE_" in TOKEN: raise RuntimeError("Set BOT_TOKEN in .env before starting the bot.")
    await init_db(); bot = Bot(TOKEN); await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
