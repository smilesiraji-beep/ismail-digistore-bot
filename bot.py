import asyncio
import os
import uuid
from decimal import Decimal, InvalidOperation

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, CallbackQuery, ReplyKeyboardMarkup
from dotenv import load_dotenv

from ventebot import VenteBotClient, VenteBotError, extract_products, money

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
DB = os.getenv("DB_PATH", "digistore.db")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
vente = VenteBotClient()

kb = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="🛍️ Products"), KeyboardButton(text="🔥 Offers")],
    [KeyboardButton(text="🎁 Referral Offers"), KeyboardButton(text="📦 My Orders")],
    [KeyboardButton(text="💳 Payment"), KeyboardButton(text="💬 Support")],
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
    "Autodesk Admin Dashboard Access(3000 invite",
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
]

def _catalog_key(text: str) -> str:
    return "".join(ch.lower() for ch in str(text) if ch.isalnum())

def sort_storebat_products(items):
    order = {_catalog_key(name): i for i, name in enumerate(STOREBAT_PRODUCT_ORDER)}
    def rank(p):
        name = str(p.get("name") or p.get("title") or "")
        key = _catalog_key(name)
        if key in order:
            return (order[key], name.lower())
        # Prefix matching handles supplier labels that contain extra text after the visible name.
        for wanted, idx in order.items():
            if wanted and (key.startswith(wanted) or wanted.startswith(key)):
                return (idx, name.lower())
        return (len(order) + 1, name.lower())
    return sorted(items, key=rank)


ADMIN_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="💵 Product Prices", callback_data="admin:prices"), InlineKeyboardButton(text="🧾 Bulk Price Update", callback_data="admin:bulk_prices")],
    [InlineKeyboardButton(text="📦 Pending Payments", callback_data="admin:pending")],
    [InlineKeyboardButton(text="🎁 Referral Campaigns", callback_data="admin:ref_help")],
    [InlineKeyboardButton(text="💳 Payment Settings", callback_data="admin:payment_help")],
    [InlineKeyboardButton(text="🔥 Offers", callback_data="admin:offer_help"), InlineKeyboardButton(text="💬 Support", callback_data="admin:support_help")],
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
        CREATE TABLE IF NOT EXISTS prices(
          product_id TEXT PRIMARY KEY, selling_price_usd TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders(
          id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL,
          product_id TEXT NOT NULL, product_name TEXT, quantity INTEGER DEFAULT 1,
          amount_usd TEXT, supplier_order_id TEXT, status TEXT DEFAULT 'awaiting_payment',
          activation_identifier TEXT, payment_reference TEXT,
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
            "payment_reference": "TEXT", "idempotency_key": "TEXT"
        }.items():
            if name not in cols:
                await db.execute(f"ALTER TABLE orders ADD COLUMN {name} {ddl}")
        ref_cols = {r[1] for r in await (await db.execute("PRAGMA table_info(referral_campaigns)")).fetchall()}
        for name, ddl in {"product_name":"TEXT", "starts_at":"TEXT", "ends_at":"TEXT"}.items():
            if name not in ref_cols:
                await db.execute(f"ALTER TABLE referral_campaigns ADD COLUMN {name} {ddl}")
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

async def display_price(product: dict):
    # Customer-facing price: ONLY the admin-defined selling price.
    # Supplier cost must never leak to customers.
    pid = product.get("id") or product.get("product_id") or product.get("uuid")
    return await custom_price(str(pid)) if pid is not None else None

async def get_product(product_id: str):
    data = await vente.products(lang="en")
    for p in extract_products(data):
        pid = str(p.get("id") or p.get("product_id") or p.get("uuid") or "")
        if pid == str(product_id):
            return p
    return None

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

async def product_list_markup():
    items = sort_storebat_products(extract_products(await vente.products(lang="en")))
    rows = []
    for p in items:
        pid = str(p.get("id") or p.get("product_id") or p.get("uuid") or "")
        if not pid:
            continue
        name = str(p.get("name") or p.get("title") or "Product")
        price = await display_price(p)
        price_text = f"${price:.2f}" if isinstance(price, Decimal) else "Unavailable"
        label = f"{p.get('emoji') or '📦'} {name} — {price_text}"
        if len(label) > 58:
            label = f"{p.get('emoji') or '📦'} {name[:35].rstrip()}… — {price_text}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"product:{pid}")])
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
    pay_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 I Paid — Submit Transaction ID", callback_data=f"payref:{oid}")],
        [InlineKeyboardButton(text="❌ Cancel Order", callback_data=f"cancel:{oid}")]
    ])
    instructions = await get_setting("payment_instructions", "Binance Pay details are not configured yet. Please contact support.")
    await c.message.answer(
        f"🛒 <b>New Order</b>\n🧾 Order: #{oid}\n📦 Product: {name}\n🔢 Quantity: {qty}\n💰 Total: ${total:.2f}\n\n"
        f"💳 <b>Payment Instructions</b>\n{instructions}\n\n"
        "After paying the exact amount, tap the button below and send your Binance transaction/order reference.",
        parse_mode="HTML", reply_markup=pay_kb)

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
        row=await (await db.execute("SELECT 1 FROM orders WHERE id=? AND telegram_id=? AND status='awaiting_payment'",(oid,c.from_user.id))).fetchone()
        if not row: await c.answer("Order is not awaiting payment.",show_alert=True); return
        await db.execute("INSERT INTO user_input_state(telegram_id,action,order_id) VALUES(?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET action=excluded.action,order_id=excluded.order_id,product_id=NULL,unit_price_usd=NULL,product_name=NULL,created_at=CURRENT_TIMESTAMP",(c.from_user.id,"payment_ref",oid)); await db.commit()
    await c.message.answer(f"💳 Send the Binance Transaction ID / Order ID for Order #{oid} in the message box now.")
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

@dp.callback_query(F.data == "admin:prices")
async def admin_prices(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    try:
        items = sort_storebat_products(extract_products(await vente.products(lang="en")))
    except VenteBotError as e:
        await c.message.answer(f"⚠️ Could not load products: {e}")
        return await c.answer()
    if not items:
        await c.message.answer("🛍️ No products are currently available.")
        return await c.answer()
    rows = []
    for p in items:
        pid = str(p.get("id") or p.get("product_id") or p.get("uuid") or "")
        if not pid:
            continue
        name = str(p.get("name") or p.get("title") or "Product")
        selling = await custom_price(pid)
        selling_text = f"${selling:.2f}" if isinstance(selling, Decimal) else "Not set"
        stock = p.get("stock")
        stock_text = f"📦 {stock}" if stock is not None else "📦 —"
        label = f"ID {pid} | {p.get('emoji') or '📦'} {name} | {selling_text} | {stock_text}"
        if len(label) > 60:
            label = f"ID {pid} | {p.get('emoji') or '📦'} {name[:23].rstrip()}… | {selling_text} | {stock_text}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:price_item:{pid}")])
    rows.append([InlineKeyboardButton(text="🧾 Bulk Price Update", callback_data="admin:bulk_prices")])
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    await c.message.answer(
        "💵 <b>Product Prices</b>\n\n"
        "Tap a product to see its ID and price-setting command.",
        parse_mode="HTML",
        reply_markup=markup,
    )
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
    selling = await custom_price(pid)
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


@dp.callback_query(F.data == "admin:pending")
async def admin_pending(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS: return await c.answer("Not authorized", show_alert=True)
    async with aiosqlite.connect(DB) as db:
        rows = await (await db.execute("SELECT id,telegram_id,product_name,amount_usd,payment_reference,activation_identifier FROM orders WHERE status='payment_submitted' ORDER BY id LIMIT 20")).fetchall()
    if not rows:
        await c.message.answer("✅ No payments are waiting for approval.")
    else:
        for oid,uid,name,amount,ref,act in rows:
            keys=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"✅ Approve #{oid}", callback_data=f"admin:approve:{oid}"), InlineKeyboardButton(text=f"❌ Reject #{oid}", callback_data=f"admin:reject:{oid}")]])
            await c.message.answer(f"🧾 #{oid} • {name or 'Product'}\\n👤 {uid}\\n💵 ${amount or '—'}\\n💳 Ref: {ref or '—'}\\n🔑 ID: {act or 'Not supplied'}", reply_markup=keys)
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
            await db.execute("UPDATE orders SET status=? WHERE id=?",(str(status),oid)); await db.commit()
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

@dp.callback_query(F.data == "admin:bulk_prices")
async def admin_bulk_prices(c: CallbackQuery):
    if c.from_user.id not in ADMIN_IDS:
        return await c.answer("Not authorized", show_alert=True)
    await c.message.answer(
        "🧾 Bulk Price Update\n\nSend one message in this format:\n\n"
        "/bulkprice\n105=1.50\n130=12.00\n93=2.00\n\n"
        "Use PRODUCT_ID=USD_PRICE on each line."
    )
    await c.answer()

@dp.message(Command("bulkprice"))
async def bulkprice(m: Message):
    if m.from_user.id not in ADMIN_IDS:
        return
    lines = (m.text or "").splitlines()[1:]
    updates = []
    errors = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if "=" not in line:
            errors.append(line)
            continue
        pid, price_text = [x.strip() for x in line.split("=", 1)]
        try:
            val = Decimal(price_text)
            if not pid or val < 0:
                raise InvalidOperation
            updates.append((pid, f"{val:.2f}"))
        except (InvalidOperation, ValueError):
            errors.append(line)
    if not updates:
        return await m.answer("⚠️ No valid prices found. Use:\n/bulkprice\n105=1.50\n130=12.00")
    async with aiosqlite.connect(DB) as db:
        await db.executemany(
            "INSERT INTO prices(product_id,selling_price_usd) VALUES(?,?) "
            "ON CONFLICT(product_id) DO UPDATE SET selling_price_usd=excluded.selling_price_usd",
            updates,
        )
        await db.commit()
    text = f"✅ {len(updates)} product price(s) updated successfully."
    if errors:
        text += "\n⚠️ Skipped invalid line(s): " + ", ".join(errors[:10])
    await m.answer(text)

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

@dp.message(F.text == "💳 Payment")
async def payment(m: Message):
    instructions = await get_setting("payment_instructions", "Payment method has not been configured yet. Please contact support.")
    await m.answer(f"💳 Payment — USD ($)\n\n{instructions}")
@dp.message(Command("setsupport"))
async def setsupport(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    parts=(m.text or "").split(maxsplit=1)
    if len(parts)<2: return await m.answer("Usage: /setsupport YOUR_SUPPORT_TEXT_OR_USERNAME")
    await set_setting_value("support_contact",parts[1].strip()); await m.answer("✅ Support information updated.")

@dp.message(F.text == "💬 Support")
async def support(m: Message):
    text=await get_setting("support_contact","Support contact has not been configured yet.")
    await m.answer(f"💬 Support\n\n{text}")

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

@dp.message(F.text)
async def pending_text_input(m: Message):
    # Handles only text explicitly requested by a previous button (custom quantity / payment reference).
    async with aiosqlite.connect(DB) as db:
        state=await (await db.execute("SELECT action,order_id,product_id FROM user_input_state WHERE telegram_id=?",(m.from_user.id,))).fetchone()
    if not state: return
    action, oid, pid = state
    text=(m.text or "").strip()
    if action == "admin_set_price":
        if m.from_user.id not in ADMIN_IDS:
            return
        try:
            val = Decimal(text)
            if val < 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            return await m.answer("⚠️ Send a valid USD price, for example: 8.00")
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT INTO prices(product_id,selling_price_usd) VALUES(?,?) "
                "ON CONFLICT(product_id) DO UPDATE SET selling_price_usd=excluded.selling_price_usd",
                (pid, f"{val:.2f}"),
            )
            await db.execute("DELETE FROM user_input_state WHERE telegram_id=?", (m.from_user.id,))
            await db.commit()
        await m.answer(f"✅ Selling price updated to ${val:.2f}.")
        return
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
            return await m.answer("⚠️ Please send a valid Binance Transaction ID / Order ID.")
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
            row=await (await db.execute("SELECT product_name,amount_usd,activation_identifier FROM orders WHERE id=?",(oid,))).fetchone()
        name,amount,act=row if row else ("Product","—",None)
        keys=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"✅ Approve #{oid}",callback_data=f"admin:approve:{oid}"),InlineKeyboardButton(text=f"❌ Reject #{oid}",callback_data=f"admin:reject:{oid}")]])
        for admin_id in ADMIN_IDS:
            try: await m.bot.send_message(admin_id,f"💳 <b>Payment submitted</b>\n🧾 Order #{oid}\n📦 {name}\n👤 {m.from_user.id}\n💵 ${amount}\n🔎 Ref: <code>{text}</code>\n🔑 ID: {act or 'Not supplied'}",parse_mode="HTML",reply_markup=keys)
            except Exception: pass

async def main():
    if not TOKEN or "PASTE_" in TOKEN: raise RuntimeError("Set BOT_TOKEN in .env before starting the bot.")
    await init_db(); bot = Bot(TOKEN); await dp.start_polling(bot)
if __name__ == "__main__": asyncio.run(main())
