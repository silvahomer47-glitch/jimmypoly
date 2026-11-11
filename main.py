from config import API_TOKEN
from database import (
    init_db, USE_PG,
    db_register_user, db_set_user_verified, db_load_verified_users,
    db_user_count, db_get_all_user_ids, db_get_all_users,
    db_save_order, db_get_orders, db_get_order, db_get_pending_orders,
    db_mark_expired, db_complete_order, db_get_all_orders,
    db_get_order_items, db_order_count, db_completed_order_count, db_load_existing_expired,
    db_get_expired_orders, db_get_order_status_counts,
    db_get_cart, db_add_to_cart, db_remove_from_cart, db_clear_cart, db_get_cart_total,
    db_get_wishlist, db_wishlist_has, db_toggle_wishlist,
    db_add_chat_message, db_get_chat_history, db_clear_chat_history,
    db_add_review, db_get_reviews, db_review_count,
    db_set_warning_msg_id, db_load_warning_msg_ids,
)
from reviews_data import (
    ALL_REVIEWS, BASE_TOTAL, BASE_MONTHLY, BASE_WEEKLY, STAR_DIST,
    REVIEWS_PER_PAGE, STARS, filter_reviews, format_review,
)
import telebot
from telebot import types
import logging
import threading
import random
import time
import qrcode
import io
import requests
import os
import re
import sys
import html
from datetime import datetime

# The startup logs and product names contain arrows, emoji and £ signs. A plain
# Windows console is cp1252 and raises UnicodeEncodeError on those, killing the
# bot before it ever polls, so force the streams to UTF-8 where we can.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass  # already UTF-8, or not a real stream (redirected output)

logging.basicConfig(level=logging.INFO)

bot = telebot.TeleBot(API_TOKEN)

# Telegram occasionally times out on us, especially on the first call after a
# deploy. Retry those inside the API layer instead of letting them surface.
telebot.apihelper.RETRY_ON_ERROR = True

BTC_ADDRESS = "bc1qasj4vu08gcs2qun0jah5vxrxpncjlvncfyfunh"
LTC_ADDRESS = "ltc1quth860lxp2j3r90la2jler34vlzdupptws2df7"
XMR_ADDRESS = os.getenv("XMR_ADDRESS", "485ij2g5iHD72xWgcnEGjUeABBQYydjToMiMCELVjhJschzu7sQnqgTBGx6nwvjWFy9E26SLpPhKrhmUJdKQc3x8Fch7nd9")
# Fallback rates used only if the live fetch fails
BTC_RATE_FALLBACK = 80000
LTC_RATE_FALLBACK = 70
XMR_RATE_FALLBACK = 150

_rate_cache = {"rates": None, "ts": 0}
_RATE_TTL = 300  # refresh every 5 minutes

def get_crypto_rates():
    """Return (btc_gbp, ltc_gbp, xmr_gbp). Cached for 5 min, falls back to constants."""
    now = time.time()
    if _rate_cache["rates"] and now - _rate_cache["ts"] < _RATE_TTL:
        return _rate_cache["rates"]
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price"
            "?ids=bitcoin,litecoin,monero&vs_currencies=gbp",
            timeout=8
        )
        data = r.json()
        rates = (
            data["bitcoin"]["gbp"],
            data["litecoin"]["gbp"],
            data["monero"]["gbp"],
        )
        _rate_cache["rates"] = rates
        _rate_cache["ts"] = now
        return rates
    except Exception as e:
        logging.warning(f"Live rate fetch failed: {e} — using fallback rates")
        return BTC_RATE_FALLBACK, LTC_RATE_FALLBACK, XMR_RATE_FALLBACK

DELIVERY_COSTS = {
    "Tracked24 - £5.00": 5.00,
    "Special Delivery Guaranteed by 1PM - £10.00": 10.00,
}

DISCOUNT_CODES = {
    "MCLOVE": 0.10,  # 10% off all products
}
ADMIN_CHAT_ID = 7742558158          # primary admin — kept for reference only
ADMIN_IDS = {7742558158, 7848018854}

# "today at 08:58 ★★★★★ — £77.23" — the admin's "Add Rating" header line,
# written exactly like a review already looks on the ratings page.
_ADMIN_REVIEW_HEADER_RE = re.compile(
    r"^(.+?) at (\d{1,2}:\d{2}) ([★☆]{5}) — £([\d,]+(?:\.\d+)?)$")

def notify_admins(text, reply_markup=None, track_user=None):
    """Send a notification to every admin.

    When track_user is given, each admin's own copy is registered in
    admin_reply_map, so any of them can reply just by replying to it.
    """
    sent = []
    for aid in ADMIN_IDS:
        try:
            m = bot.send_message(aid, text, parse_mode="HTML", reply_markup=reply_markup)
            if track_user is not None:
                admin_reply_map[m.message_id] = track_user
            sent.append(m)
        except Exception as e:
            logging.warning(f"Failed to notify admin {aid}: {e}")
    return sent

def forward_media_to_admins(from_chat_id, msg_id, header, reply_markup=None, track_user=None):
    """Copy a user's media to every admin, wrapped in a header and a reply button."""
    for aid in ADMIN_IDS:
        try:
            hdr = bot.send_message(aid, header, parse_mode="HTML")
            med = bot.copy_message(aid, from_chat_id, msg_id)
            bot.send_message(aid, "👆 Media from user above", reply_markup=reply_markup)
            if track_user is not None:
                admin_reply_map[hdr.message_id] = track_user
                if med is not None:
                    admin_reply_map[med.message_id] = track_user
        except Exception as e:
            logging.warning(f"Failed to forward media to admin {aid}: {e}")

PGP_KEY = (
    "-----BEGIN PGP PUBLIC KEY BLOCK-----\n"
    "Comment: User ID:\tMcWhites <mcwhites@protonmail.com>\n"
    "Comment: Valid from:\t09/12/2024 16:24\n"
    "Comment: Type:\t255-bit EdDSA (secret key available)\n"
    "Comment: Usage:\tSigning, Encryption, Certifying User IDs\n"
    "Comment: Fingerprint:\t2F466E5F485F949C2790BF927DBA9E3C07B1424B\n\n"
    "mDMEZ1cLrBYJKwYBBAHaRw8BAQdAqpUBJP7dAiXKBST2i2V+ho0tSlHcxrJjw07C\n"
    "UKD+fza0Ik1jV2hpdGVzIDxtY3doaXRlc0Bwcm90b25tYWlsLmNvbT6IkwQTFgoA\n"
    "OwIbAwULCQgHAgIiAgYVCgkICwIEFgIDAQIeBwIXgBYhBC9Gbl9IX5ScJ5C/kn26\n"
    "njwHsUJLBQJnVwxXAAoJEH26njwHsUJL/roA/14lwasnryyiQgvjo55ncNxm03SF\n"
    "BCnpdQ0NwSch4DqHAP4z5imtWdUuR/RieDNB5ii4hlZDu59HZX2nD5aXeusTCbg4\n"
    "BGdXC6wSCisGAQQBl1UBBQEBB0AQUWeOZxFnwmt6UBXp/v1tCkYUganaIrBldzpF\n"
    "KGIZCgMBCAeIeAQYFgoAIAIbDBYhBC9Gbl9IX5ScJ5C/kn26njwHsUJLBQJnVwxt\n"
    "AAoJEH26njwHsUJL1FYA/3AiP/Ok8kE1E77nVGYJ0UY8+YedU4uiSiWdmKjOiqML\n"
    "AQCq3dm2uZl6jZgviJlzm2mf6P+dunSPxTMA6QQ6aFkfAA==\n"
    "=5Q9o\n"
    "-----END PGP PUBLIC KEY BLOCK-----"
)

def _send_pgp(chat_id):
    buf = io.BytesIO(PGP_KEY.encode("utf-8"))
    buf.name = "mcwhites_pgp.txt"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🗑 Delete Message", callback_data="delete_this_msg"))
    bot.send_document(chat_id, buf, caption="Public PGP key", reply_markup=kb,
                      reply_to_message_id=user_last_msg_id.get(chat_id))

# =========================
# PRODUCT IMAGES — local file cache system
# Drop images into the "images/" folder next to main.py.
# Name each file after the product prefix, e.g. p_temple.jpg
# Supported extensions: jpg, jpeg, png, webp
# If no image file exists for a product it just shows text as before.
# =========================

_IMAGES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "images")
_FILES_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files")
_image_id_cache = {}

def get_product_image(prefix, chat_id):
    """Upload image for prefix once, cache the file_id, return it on subsequent calls."""
    if prefix in _image_id_cache:
        return _image_id_cache[prefix]
    for ext in ("jpg", "jpeg", "png", "webp"):
        path = os.path.join(_IMAGES_DIR, f"{prefix}.{ext}")
        if os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    sent = bot.send_photo(chat_id, f)
                file_id = sent.photo[-1].file_id
                _image_id_cache[prefix] = file_id
                try: bot.delete_message(chat_id, sent.message_id)
                except: pass
                return file_id
            except Exception as e:
                logging.warning(f"Failed to cache image for '{prefix}': {e}")
                return None
    return None

# Built lazily after PRODUCTS is defined
_fn_to_prefix = None

def _fn_prefix(fn):
    global _fn_to_prefix
    if _fn_to_prefix is None:
        _fn_to_prefix = {v[0]: k for k, v in PRODUCTS.items()}
    return _fn_to_prefix.get(fn)

# All database functions are in database.py and imported at the top.

# =========================
# BLOCKCYPHER PAYMENT MONITOR
# =========================

SATOSHI = 1e8
order_baselines = {}
baseline_btc = None
baseline_ltc = None

def get_received_amount_btc(address):
    try:
        r = requests.get(f"https://api.blockcypher.com/v1/btc/main/addrs/{address}/balance", timeout=10)
        if r.status_code == 200:
            return r.json().get("total_received", 0) / SATOSHI
    except Exception as e:
        logging.warning(f"BTC check failed: {e}")
    return 0

def get_received_amount_ltc(address):
    try:
        r = requests.get(f"https://api.blockcypher.com/v1/ltc/main/addrs/{address}/balance", timeout=10)
        if r.status_code == 200:
            return r.json().get("total_received", 0) / SATOSHI
    except Exception as e:
        logging.warning(f"LTC check failed: {e}")
    return 0

def init_baselines():
    global baseline_btc, baseline_ltc
    baseline_btc = get_received_amount_btc(BTC_ADDRESS)
    baseline_ltc = get_received_amount_ltc(LTC_ADDRESS)
    logging.info(f"Baselines — BTC: {baseline_btc}, LTC: {baseline_ltc}")

def payment_monitor():
    time.sleep(10)
    while True:
        try:
            db_mark_expired()
            pending = db_get_pending_orders()
            if pending:
                current_btc = get_received_amount_btc(BTC_ADDRESS)
                current_ltc = get_received_amount_ltc(LTC_ADDRESS)
                for row in pending:
                    invoice, crypto_amount, crypto_symbol, crypto_address, chat_id, expires_at = row
                    baseline = order_baselines.get(invoice,
                        baseline_btc if crypto_symbol == "BTC" else baseline_ltc)
                    if crypto_symbol == "BTC":
                        received = max(0, current_btc - baseline)
                    elif crypto_symbol == "LTC":
                        received = max(0, current_ltc - baseline)
                    else:
                        continue
                    logging.info(f"Invoice #{invoice} — need {crypto_amount} {crypto_symbol}, got {received}")
                    if crypto_amount and received >= float(crypto_amount) * 0.99:
                        db_complete_order(invoice)
                        order_baselines.pop(invoice, None)
                        try:
                            bot.send_message(chat_id,
                                f"✅ <b>Payment Confirmed!</b>\n\n"
                                f"Invoice #{invoice} — {crypto_amount} {crypto_symbol}\n\nThank you! 🎉",
                                parse_mode="HTML")
                        except: pass
                        notify_admins(
                            f"✅ <b>Payment received!</b>\nInvoice #{invoice} — {crypto_amount} {crypto_symbol}\nUser: <code>{chat_id}</code>")
        except Exception as e:
            logging.warning(f"Payment monitor error: {e}")
        time.sleep(90)

def order_expiry_watcher():
    while True:
        try:
            db_mark_expired()
            rows = db_get_expired_orders()
            for invoice, cid in rows:
                if invoice not in notified_expired:
                    notified_expired.add(invoice)
                    try:
                        kb = types.InlineKeyboardMarkup()
                        kb.add(types.InlineKeyboardButton("Order Details", callback_data=f"order_{invoice}"))
                        bot.send_message(
                            cid,
                            f"Order {invoice} has been cancelled. Payment has not been received within 3 hours.",
                            reply_markup=kb
                        )
                    except Exception as e:
                        logging.warning(f"Failed to notify expiry for invoice {invoice}: {e}")
        except Exception as e:
            logging.warning(f"Expiry error: {e}")
        time.sleep(60)

# =========================
# STATE STORAGE
# =========================

user_quantity = {}
user_discount = {}
user_payment = {}
user_address = {}
user_delivery = {}
used_invoices = set()
waiting_for_qty = {}
waiting_for_discount = {}
waiting_for_address = {}
waiting_for_contact = set()
waiting_for_announcement_users = set()
waiting_for_announcement_product = set()
pending_announcements = {}
user_warning_msg_id = {}
user_main_menu_msg_id = {}
user_last_msg_id = {}          # last incoming message id — used for reply headers
user_review_product = {}
waiting_for_admin_review = set()
pending_admin_review = {}
user_pending_invoice = {}
notified_expired = set()
admin_reply_map = {}
waiting_for_admin_reply = {}
verified_users = set()
user_secret_phrase = {}
waiting_for_passphrase = set()

# Real browsing history, so Back returns to the screen the customer actually
# came from rather than a fixed per-product/per-category default. See
# nav_push/nav_pop below.
nav_stack = {}      # chat_id -> list of screen keys visited, oldest first
nav_current = {}    # chat_id -> the screen key currently on display

_NAV_STACK_LIMIT = 20

def nav_push(chat_id, leaving_key):
    """Record the screen being left so a later Back can return to it."""
    if leaving_key is None:
        return
    stack = nav_stack.setdefault(chat_id, [])
    stack.append(leaving_key)
    if len(stack) > _NAV_STACK_LIMIT:
        del stack[0]

def _nav_target_valid(key):
    """"main_back" is a real, legitimate Back target (the main menu) even
    though it lives outside SCREEN_IMAGES/PRODUCTS - it's resolved by its
    own dispatch branch, not the generic screen/product lookup.
    """
    return key == "main_back" or key in SCREEN_IMAGES or key in PRODUCTS

def nav_pop(chat_id, fallback):
    """Pop the real previous screen, skipping anything no longer valid.
    Falls back to the caller's static target, then to the root, so a
    thin or stale history never leaves Back doing nothing.
    """
    stack = nav_stack.get(chat_id) or []
    while stack:
        candidate = stack.pop()
        if _nav_target_valid(candidate):
            return candidate
    if _nav_target_valid(fallback):
        return fallback
    return "listings"

PHRASE_MIN_LEN = 4
PHRASE_MAX_LEN = 100
OFFICIAL_SITE = "tsbots.to"
PUBLIC_CHAT_URL = "https://matrix.to/#/#mcwhitegeneralchat:vendor.tess.ht"
GUIDE_URL = "https://matrix.to/#/#mcwhitegeneralchat:vendor.tess.ht"

def _esc(s):
    """Escape a user-supplied string so it is safe inside parse_mode=HTML text."""
    return html.escape(str(s), quote=False)

def get_secret_phrase(chat_id):
    return user_secret_phrase.get(chat_id, "Not set")

# --- Screen 1: intro ---------------------------------------------------------
def phrase_intro_content():
    text = ("🔐 <b>You need to set a secret phrase to access the bot.</b> This will then appear with "
            "every verified bot. The secret phrase allows you to stay safe and buy only from verified stores.")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Set your own secret phrase", callback_data="phrase_set_own"))
    keyboard.add(types.InlineKeyboardButton("About secret phrase", callback_data="phrase_about"))
    return text, keyboard

# --- Screen 2: about ---------------------------------------------------------
def phrase_about_content():
    text = ("The <b>secret phrase system</b> is made to <b>protect</b> you from being scammed using fake bots.\n\n"
            "1. No one else will know your passphrase, and it is important to <b>remember</b> and "
            "<b>always check</b> that your <b>correct passphrase</b> is shown when using our bots.\n"
            "2. If a McWhites vendor decides to disconnect their bot from the market, you can avoid "
            "being scammed by making sure the <b>passphrase</b> is still <b>correct</b>.\n\n"
            "The first time you use the bot you will be asked to enter your passphrase. This passphrase "
            "will be displayed <b>each time</b> in each McWhites bot, after the /start command, when you "
            "<b>create an order</b> and <b>open a chat</b>.\n\n"
            "There are many bots on Telegram so please be cautious when using any of them, especially ones "
            "that aren't affiliated with McWhites. We won't be held responsible if you are scammed by a fake "
            "bot, these measures are in place to help as much as we are able.")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("I understand", callback_data="phrase_about_ok"))
    return text, keyboard

# --- Screen 3: phrase entry --------------------------------------------------
def phrase_entry_content():
    text = (f"Send your phrase to the chat in one message. The phrase must be "
            f"{PHRASE_MIN_LEN}–{PHRASE_MAX_LEN} characters.\n\n"
            "⚠️ WE WILL NEVER ASK FOR YOUR PASSPHRASE AGAIN! BEWARE OF IMPOSTERS!")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("About secret phrase", callback_data="phrase_about"))
    return text, keyboard

# --- Screen 4: saved + confirmation -----------------------------------------
PHRASE_SAVED_TEXT = "🎊 The phrase is saved. No legitimate bot will ever ask for the phrase again."

def phrase_check_content(chat_id, yes_cb="phrase_check_yes"):
    text = (f"🔒 <b>Your Secret Phrase:</b> {_esc(get_secret_phrase(chat_id))}\n\n"
            f"🔑 This phrase was set when you first launched the official McWhites bot from "
            f"{OFFICIAL_SITE}. If it doesn't match or you had to create a new one, you may be using a scam bot.\n\n"
            f"Is this your correct secret phrase and did you get the bot link from {OFFICIAL_SITE}?")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.row(
        types.InlineKeyboardButton("Yes", callback_data=yes_cb),
        types.InlineKeyboardButton("No", callback_data="phrase_check_no")
    )
    return text, keyboard

# --- Screen 5: private-message warning --------------------------------------
def phrase_private_warning_content():
    text = ("⚠️ AVOID ALL ACCOUNTS MESSAGING YOU PRIVATELY.\n\n"
            "Payments are only accepted through this bot, and the vendor can only be contacted via the "
            "Contact button here. If unsure, confirm with our community in the public chat.\n\n"
            "Do not trust or reply to anyone asking for payment or contact outside this bot.")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("I understand and agree", callback_data="phrase_agree"))
    keyboard.add(types.InlineKeyboardButton("Need more help?", url=PUBLIC_CHAT_URL))
    return text, keyboard

# --- Screen 6: group chats ---------------------------------------------------
def phrase_groupchat_content():
    text = ("🚀 <b>THE MCWHITES GROUP CHATS</b> moved to our own private servers, so they can't keep "
            "getting reported and deleted all the time.\n\n"
            "💬 Less scammers, permanent chats, and no more censorship.\n\n"
            "👇 Want to join? Follow the guide below.")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Guide", url=GUIDE_URL))
    keyboard.add(types.InlineKeyboardButton("I understand", callback_data="phrase_groups_ok"))
    return text, keyboard

# --- Screen 7: standing warning header + main menu ---------------------------
def phrase_warning_text(chat_id):
    return (
        f"⚠️ Your secret phrase: <b>{_esc(get_secret_phrase(chat_id))}</b>\n\n"
        f"When you first started a legit McWhites bot from the official website {OFFICIAL_SITE}, "
        "this is the phrase you created. If this is not the phrase, or if you had to set up a new "
        "phrase again, you may have landed on a scambot.\n\n"
        "If you have any doubts, please chat with real members of our community via our public chat "
        "to ensure you started a legitimate bot."
    )

def post_warning_header(chat_id):
    """Post the secret-phrase header and remember its id so later messages can reply to it.

    The id is persisted, so the reply header still resolves after a redeploy.
    """
    msg = bot.send_message(chat_id, phrase_warning_text(chat_id), parse_mode="HTML")
    user_warning_msg_id[chat_id] = msg.message_id
    db_set_warning_msg_id(chat_id, msg.message_id)
    return msg.message_id

def show_main_with_warning(chat_id):
    nav_stack.pop(chat_id, None)
    nav_current.pop(chat_id, None)
    warning_mid = post_warning_header(chat_id)
    caption, keyboard = main_menu_content(chat_id)
    menu_msg = fresh_screen(chat_id, caption, keyboard, reply_to=warning_mid, image_key="menu")
    user_main_menu_msg_id[chat_id] = menu_msg.message_id

def get_quantity(chat_id):
    return user_quantity.get(chat_id, 3.5)

def set_quantity(chat_id, qty):
    user_quantity[chat_id] = round(qty, 1)

def get_cart(chat_id):
    return db_get_cart(chat_id)

def add_to_cart(chat_id, name, qty, price, unit="gr"):
    db_add_to_cart(chat_id, name, qty, price, unit)

def get_cart_total(chat_id):
    return db_get_cart_total(chat_id)

def remove_from_cart(chat_id, index):
    db_remove_from_cart(chat_id, index)

def generate_invoice():
    while True:
        num = random.randint(1000, 9999)
        if num not in used_invoices:
            used_invoices.add(num)
            return num

def convert_to_crypto(gbp_total, method):
    btc_rate, ltc_rate, xmr_rate = get_crypto_rates()
    if method == "Bitcoin":
        return round(gbp_total / btc_rate, 8), "BTC", BTC_ADDRESS
    elif method == "Litecoin":
        return round(gbp_total / ltc_rate, 8), "LTC", LTC_ADDRESS
    elif method == "Monero":
        return round(gbp_total / xmr_rate, 8), "XMR", XMR_ADDRESS
    return None, None, None

def generate_qr_image(data):
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = io.BytesIO()
    bio.name = "qr.png"
    img.save(bio, "PNG")
    bio.seek(0)
    return bio

def save_order(chat_id):
    invoice = user_pending_invoice.pop(chat_id, None) or generate_invoice()
    cart = get_cart(chat_id)
    payment  = user_payment.get(chat_id)
    address  = user_address.get(chat_id)
    delivery = user_delivery.get(chat_id)
    discount = user_discount.get(chat_id)
    delivery_cost = DELIVERY_COSTS.get(delivery, 0.0)
    cart_subtotal = get_cart_total(chat_id)
    discount_rate = DISCOUNT_CODES.get((discount or "").upper(), 0.0)
    total = round(cart_subtotal * (1 - discount_rate) + delivery_cost, 2)
    crypto_amount, crypto_symbol, crypto_address = convert_to_crypto(total, payment)
    if crypto_symbol == "BTC":
        order_baselines[invoice] = get_received_amount_btc(BTC_ADDRESS)
    elif crypto_symbol == "LTC":
        order_baselines[invoice] = get_received_amount_ltc(LTC_ADDRESS)
    db_save_order(chat_id, invoice, total, crypto_amount, crypto_symbol, crypto_address,
                  payment, address, delivery, discount, cart)
    db_clear_cart(chat_id)
    user_discount.pop(chat_id, None); user_payment.pop(chat_id, None)
    user_address.pop(chat_id, None);  user_delivery.pop(chat_id, None)
    return invoice, total, db_get_order(invoice)

def cart_button(chat_id):
    total = get_cart_total(chat_id)
    return f"🛒 £{total}" if total > 0 else "🛒 £0.00"

def format_status(order):
    status = order["status"]
    if status == "completed":
        ts = datetime.fromtimestamp(order["completed_at"]).strftime("%d/%m/%Y %H:%M") if order.get("completed_at") else "N/A"
        return f"✅ Completed — {ts}"
    elif status == "expired":
        return "❌ Expired — payment not received within 3 hours"
    else:
        remaining = max(0, order["expires_at"] - time.time())
        h = int(remaining // 3600); m = int((remaining % 3600) // 60)
        return f"🕐 Pending Payment\n↳ time left {h}h:{m:02d}m"

def _announcement_kb(product_key):
    kb = types.InlineKeyboardMarkup()
    if product_key and product_key in PRODUCTS:
        _, _, _, product_name = PRODUCTS[product_key]
        kb.add(types.InlineKeyboardButton(product_name, callback_data=f"announce_open_{product_key}"))
    kb.add(types.InlineKeyboardButton("Close notification", callback_data="close_announcement"))
    return kb

def _send_announcement(uid, msg_type, text, file_id, kb, reply_to):
    if msg_type == "photo":
        return bot.send_photo(uid, file_id, caption=text, reply_markup=kb,
                              parse_mode="HTML", reply_to_message_id=reply_to)
    if msg_type == "video":
        return bot.send_video(uid, file_id, caption=text, reply_markup=kb,
                              parse_mode="HTML", reply_to_message_id=reply_to)
    return bot.send_message(uid, text, reply_markup=kb,
                            parse_mode="HTML", reply_to_message_id=reply_to)

def send_announcement_to(uid, msg_type, text, file_id, kb):
    """Deliver an announcement anchored to the user's secret-phrase header.

    The stored header id survives a redeploy, but the message itself may be gone
    (user cleared the chat, old deploy's history) — in that case Telegram rejects
    the reply, so we repost the header and anchor to the fresh one.
    """
    rid = user_warning_msg_id.get(uid)
    if rid:
        try:
            return _send_announcement(uid, msg_type, text, file_id, kb, rid)
        except Exception as e:
            logging.info(f"Reply anchor {rid} unusable for {uid} ({e}) — reposting header")
    if uid in verified_users:
        rid = post_warning_header(uid)
        return _send_announcement(uid, msg_type, text, file_id, kb, rid)
    # Not verified yet, so there is no phrase to head the message with.
    return _send_announcement(uid, msg_type, text, file_id, kb, None)

def broadcast_announcement(payload, from_chat_id):
    msg_type  = payload.get("type", "text")
    text      = payload.get("text", "")
    file_id   = payload.get("file_id")
    kb = _announcement_kb(payload.get("product_key"))
    success = 0; failed = 0
    for uid in [u for u in db_get_all_user_ids() if u not in ADMIN_IDS]:
        try:
            send_announcement_to(uid, msg_type, text, file_id, kb)
            success += 1
            time.sleep(0.05)
        except Exception as e:
            logging.warning(f"Failed to send to {uid}: {e}")
            failed += 1
    bot.send_message(from_chat_id, f"✅ Sent!\n\n📨 Delivered: {success}\n❌ Failed: {failed}", parse_mode="HTML")

def announcement_product_keyboard():
    kb = types.InlineKeyboardMarkup()
    skip_btn = types.InlineKeyboardButton("⏭ Skip — No Product", callback_data="announce_no_product")
    kb.add(skip_btn)
    keys = list(PRODUCTS.keys())
    for i in range(0, len(keys), 2):
        row = [types.InlineKeyboardButton(PRODUCTS[p][3], callback_data=f"announce_product_{p}")
               for p in keys[i:i+2]]
        kb.row(*row)
    kb.add(skip_btn)
    return kb

def send_announce_preview(chat_id, payload):
    msg_type    = payload.get("type", "text")
    text        = payload.get("text", "")
    file_id     = payload.get("file_id")
    product_key = payload.get("product_key")
    preview_kb  = _announcement_kb(product_key)
    bot.send_message(chat_id, "📢 <b>Preview (as users will see it):</b>", parse_mode="HTML")
    if msg_type == "photo":
        bot.send_photo(chat_id, file_id, caption=text, reply_markup=preview_kb, parse_mode="HTML")
    elif msg_type == "video":
        bot.send_video(chat_id, file_id, caption=text, reply_markup=preview_kb, parse_mode="HTML")
    else:
        bot.send_message(chat_id, text, reply_markup=preview_kb, parse_mode="HTML")
    confirm_kb = types.InlineKeyboardMarkup()
    confirm_kb.row(
        types.InlineKeyboardButton(f"✅ Send to All ({db_user_count() - 1} users)", callback_data="admin_confirm_announce"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="admin_cancel_announce")
    )
    bot.send_message(chat_id, "Send this announcement to all users?", reply_markup=confirm_kb)

# =========================
# CORE NAVIGATION
# =========================

def swap_photo(chat_id, message_id, new_photo_id, caption, keyboard):
    try:
        media = types.InputMediaPhoto(media=new_photo_id, caption=caption, parse_mode="HTML")
        bot.edit_message_media(media=media, chat_id=chat_id, message_id=message_id)
        bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=keyboard)
    except Exception as e:
        logging.warning(f"swap_photo failed: {e}")
        try: bot.delete_message(chat_id, message_id)
        except: pass
        try:
            bot.send_photo(chat_id, new_photo_id, caption=caption,
                           reply_markup=keyboard, parse_mode="HTML")
        except Exception as e2:
            logging.warning(f"swap_photo fallback failed: {e2}")

def edit_caption(chat_id, message_id, caption, keyboard):
    try:
        bot.edit_message_caption(caption=caption, chat_id=chat_id, message_id=message_id,
                                 reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logging.warning(f"edit_caption failed: {e}")
        bot.send_message(chat_id, caption, reply_markup=keyboard, parse_mode="HTML")

def edit_text(chat_id, message_id, text, keyboard):
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                              reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        if "message is not modified" in str(e).lower():
            return
        logging.warning(f"edit_text failed: {e}")
        bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML",
                         reply_to_message_id=user_warning_msg_id.get(chat_id))

def fresh_screen(chat_id, caption, keyboard, reply_to=None, image_key=None):
    """Send a screen as a new message, with its image if there is one.

    Used where a screen is posted fresh rather than edited in place, so the
    main menu looks the same whether you arrive by button or after /start.
    """
    rid = reply_to or user_last_msg_id.get(chat_id)
    photo_id = get_product_image(image_key, chat_id) if image_key else None
    if photo_id:
        return bot.send_photo(chat_id, photo_id, caption=caption, reply_markup=keyboard,
                              parse_mode="HTML", reply_to_message_id=rid)
    return bot.send_message(chat_id, caption, reply_markup=keyboard, parse_mode="HTML", reply_to_message_id=rid)

def go_photo_screen(chat_id, message_id, caption, keyboard, target_photo_id=None):
    try: bot.delete_message(chat_id, message_id)
    except: pass
    rid = user_last_msg_id.get(chat_id)
    if target_photo_id:
        try:
            bot.send_photo(chat_id, target_photo_id, caption=caption, reply_markup=keyboard,
                           parse_mode="HTML", reply_to_message_id=rid)
            return
        except Exception:
            pass
    bot.send_message(chat_id, caption, reply_markup=keyboard, parse_mode="HTML", reply_to_message_id=rid)

def go_text_screen(chat_id, message_id, text, keyboard, is_photo):
    if is_photo:
        try: bot.delete_message(chat_id, message_id)
        except: pass
        # reply to the warning message (if present), never to the message we just deleted
        rid = user_warning_msg_id.get(chat_id)
        bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML",
                         reply_to_message_id=rid)
    else:
        edit_text(chat_id, message_id, text, keyboard)

def open_product(chat_id, message_id, content_fn, is_photo):
    text, keyboard, image_key = content_fn(chat_id)
    lookup = image_key or _fn_prefix(content_fn)
    if lookup:
        photo_id = get_product_image(lookup, chat_id)
        if photo_id:
            swap_photo(chat_id, message_id, photo_id, text, keyboard)
            return
    go_text_screen(chat_id, message_id, text, keyboard, is_photo)

def open_screen(chat_id, message_id, text, keyboard, is_photo, image_key):
    """Show a menu screen with its image from images/<image_key>.<ext>.

    Same idea as open_product, but for screens that have no PRODUCTS entry to
    take the image name from. Falls back to a plain text screen if no file exists.
    """
    photo_id = get_product_image(image_key, chat_id) if image_key else None
    if photo_id:
        swap_photo(chat_id, message_id, photo_id, text, keyboard)
        return
    go_text_screen(chat_id, message_id, text, keyboard, is_photo)

def update_product(chat_id, message_id, content_fn, is_photo):
    text, keyboard, image_key = content_fn(chat_id)
    lookup = image_key or _fn_prefix(content_fn)
    if lookup and lookup in _image_id_cache:
        edit_caption(chat_id, message_id, text, keyboard)
    else:
        edit_text(chat_id, message_id, text, keyboard)

def build_product_keyboard(prefix, back_target, step, qty, total, chat_id, reviews=0):
    cart_label = cart_button(chat_id)
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Enter Custom Quantity", callback_data=f"{prefix}_custom_qty"))
    keyboard.row(
        types.InlineKeyboardButton(f"▼ {qty} pcs", callback_data=f"{prefix}_decrease"),
        types.InlineKeyboardButton(cart_label, callback_data="cart_main"),
        types.InlineKeyboardButton(f"▲ {qty} pcs", callback_data=f"{prefix}_increase")
    )
    keyboard.add(types.InlineKeyboardButton(f"Add to Cart: {qty} pcs [£{total}]", callback_data=f"{prefix}_add_to_cart", style="success"))
    keyboard.row(
        types.InlineKeyboardButton("Add to Wishlist", callback_data=f"{prefix}_wishlist"),
        types.InlineKeyboardButton(f"Show Reviews ({reviews:,})", callback_data=f"{prefix}_reviews")
    )
    keyboard.add(types.InlineKeyboardButton("Back", callback_data=back_target))
    return keyboard

def tier_price(qty, tiers):
    rate = tiers[0][1]
    for min_qty, r in tiers:
        if qty >= min_qty:
            rate = r
    return round(qty * rate, 2)

def tier_price_per_unit(qty, tiers, unit_size):
    """tiers store price-per-unit; total = (qty / unit_size) * rate"""
    rate = tiers[0][1]
    for min_qty, r in tiers:
        if qty >= min_qty:
            rate = r
    return round((qty / unit_size) * rate, 2)

def tier_rate(qty, tiers):
    rate = tiers[0][1]
    for min_qty, r in tiers:
        if qty >= min_qty:
            rate = r
    return rate

def build_mcw_keyboard(prefix, back_target, step, qty, total_price, chat_id, reviews=0, unit="gr", rate=None):
    # Back always returns to the section the product is actually filed under.
    # The caller's back_target is only a fallback: the older product screens
    # still name categories that were retired when the storefront was rebuilt.
    back_target = PRODUCT_SECTION.get(prefix, back_target)
    ct = get_cart_total(chat_id) if chat_id else 0.0
    in_wl = bool(chat_id and db_wishlist_has(chat_id, prefix))
    wl_label = "❤️ Remove from Wishlist" if in_wl else "Add to Wishlist"
    display_price = rate if rate is not None else total_price
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Enter Quantity Manually", callback_data=f"{prefix}_custom_qty"))
    keyboard.row(
        types.InlineKeyboardButton(f"▲ +{step:.2f}", callback_data=f"{prefix}_increase"),
        types.InlineKeyboardButton(f"🛒 £{ct:.2f}", callback_data="cart_main"),
        types.InlineKeyboardButton(f"▼ -{step:.2f}", callback_data=f"{prefix}_decrease")
    )
    keyboard.add(types.InlineKeyboardButton(f"Add to Cart : {qty:.2f} {unit} [£{display_price:.2f}]", callback_data=f"{prefix}_add_to_cart", style="success"))
    keyboard.row(
        types.InlineKeyboardButton(wl_label, callback_data=f"{prefix}_wishlist"),
        types.InlineKeyboardButton(f"{reviews:,} reviews for this product", callback_data=f"{prefix}_reviews")
    )
    keyboard.add(types.InlineKeyboardButton("Back", callback_data=f"back:{back_target}"))
    return keyboard

# =========================
# =========================
# REVIEWS
# =========================

def reviews_content(chat_id, page, stars, prefix=None, back_cb="main_back"):
    bot_sales    = db_completed_order_count()
    dynamic      = list(db_get_reviews())          # newest first from DB
    added_count  = db_review_count()
    total        = BASE_TOTAL + bot_sales + added_count
    # Merge: dynamic (newest) first, then hardcoded
    combined     = dynamic + list(ALL_REVIEWS)
    stars_filter = stars if stars != 0 else None
    reviews      = filter_reviews(prefix, stars_filter, combined)
    import math
    t_pages      = max(1, math.ceil((len(reviews) if (prefix or stars_filter) else total) / REVIEWS_PER_PAGE))

    # Page text
    start_i = (page - 1) * REVIEWS_PER_PAGE
    end_i   = start_i + REVIEWS_PER_PAGE
    page_reviews = reviews[start_i:end_i]

    shown_end = min(start_i + len(page_reviews), start_i + REVIEWS_PER_PAGE)
    if page_reviews:
        header = (
            f"Showing {start_i + 1}–{start_i + len(page_reviews)} "
            f"out of {total:,} {'reviews for this product' if prefix else 'all reviews'}\n\n"
            f"Total / Monthly / Weekly\n"
            f"= {total:,} / +{BASE_MONTHLY} / +{BASE_WEEKLY}\n\n"
        )
        body = "\n\n".join(format_review(r) for r in page_reviews)
        text = header + body
    else:
        text = f"⭐ No reviews found for page {page}."

    # Star filter counts
    mode = "p" if prefix else "g"
    if prefix:
        all_for_product = filter_reviews(prefix, source=combined)
        star_counts = {s: sum(1 for r in all_for_product if r[1] == s) for s in range(1, 6)}
        all_count   = len(all_for_product)
    else:
        star_counts = STAR_DIST
        all_count   = total

    def rv_cb(p, s):
        return f"rv_{mode}_{s}_{p}"

    kb = types.InlineKeyboardMarkup()

    # Row 1: star filters
    all_label  = f"All{'*' if stars == 0 else ''} ({all_count:,})"
    filter_row = [types.InlineKeyboardButton(all_label, callback_data=rv_cb(1, 0))]
    for s in (5, 4, 3, 2, 1):
        cnt   = star_counts.get(s, 0)
        label = f"{'★'*s}{'*' if stars == s else ''} ({cnt})"
        filter_row.append(types.InlineKeyboardButton(label, callback_data=rv_cb(1, s)))
    kb.row(*filter_row)

    # Row 2: page navigation  [p-2 p-1 p* p+1 p+2  last»]
    window = []
    for p in range(max(1, page - 2), min(t_pages, page + 2) + 1):
        label = f"{p}{'*' if p == page else ''}"
        window.append(types.InlineKeyboardButton(label, callback_data=rv_cb(p, stars)))
    if t_pages not in [p for p in range(max(1, page - 2), min(t_pages, page + 2) + 1)]:
        window.append(types.InlineKeyboardButton(f"{t_pages} »", callback_data=rv_cb(t_pages, stars)))
    kb.row(*window)

    # The per-product view's Back goes through real history (so it lands
    # back on the product, not wherever "main_back" clearing resets to);
    # the all-reviews view keeps going straight to the main menu as before.
    back_data = f"back:{back_cb}" if prefix else back_cb
    kb.add(types.InlineKeyboardButton("Back", callback_data=back_data))
    return text, kb

# =========================
# ADMIN PANEL
# =========================

def admin_panel_content():
    text = (f"🔧 <b>Admin Panel</b>\n\n"
            f"👥 Total users: <b>{db_user_count()}</b>\n"
            f"📦 Total orders: <b>{db_order_count()}</b>\n\nChoose an action:")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("📢 Send Announcement", callback_data="admin_announce"))
    keyboard.add(types.InlineKeyboardButton("👥 View User Count", callback_data="admin_users"))
    keyboard.add(types.InlineKeyboardButton("📦 View All Orders", callback_data="admin_orders"))
    keyboard.add(types.InlineKeyboardButton("❌ Close Panel", callback_data="admin_close"))
    return text, keyboard

def admin_orders_content():
    db_mark_expired()
    rows = db_get_all_orders()
    if not rows:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(" Back to Panel", callback_data="admin_back"))
        return "📦 No orders placed yet.", keyboard

    status_icons = {"pending": "🕐", "expired": "❌", "completed": "✅"}
    text = "📦 <b>All Orders:</b>\n\n"

    for row in rows[:15]:
        invoice, total, payment, status, uid, username, created_at, address, delivery, crypto_symbol, crypto_amount = row
        icon = status_icons.get(status, "•")
        uname = f"@{username}" if username else f"ID:{uid}"
        date_str = datetime.fromtimestamp(created_at).strftime("%d/%m/%Y %H:%M") if created_at else "N/A"
        address_str = address if address else "Not provided"
        delivery_str = delivery if delivery else "N/A"
        crypto_str = f"{crypto_amount:.8f} {crypto_symbol}" if crypto_amount and crypto_symbol else "N/A"

        # Fetch items for this order
        items = db_get_order_items(invoice)
        items_str = ""
        for item in items:
            name, qty, price = item
            items_str += f"      • {name} x {qty} pcs — £{price}\n"
        if not items_str:
            items_str = "      • No items found\n"

        text += (
            f"{icon} <b>Invoice #{invoice}</b>\n"
            f"   👤 {uname}\n"
            f"   🛒 <b>Items ordered:</b>\n"
            f"{items_str}"
            f"   💷 Total: £{total} via {payment or 'N/A'}\n"
            f"   🪙 {crypto_str}\n"
            f"   📦 {delivery_str}\n"
            f"   🏡 <code>{address_str}</code>\n"
            f"   🕒 {date_str}\n\n"
        )

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(" Back to Panel", callback_data="admin_back"))
    return text, keyboard

# =========================
# CONTACT SCREEN
# =========================

def contact_content(chat_id=None):
    secret_phrase = _esc(get_secret_phrase(chat_id)) if chat_id else "Not set"
    text = (f"🪖 Verify the secret phrase before sending any messages.\n\n"
            f"Your secret phrase: <b>{secret_phrase}</b>\n"
            "Last seen: recently\n\n"
            "This is not a live chat, the seller will reply as soon as he reads your messages.\n\n"
            "⚠️ Vendors cannot receive or view images/videos sent through chat, so please avoid sending media files.")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Vendor PGP key", callback_data="checkout_pgp_key"))
    keyboard.add(types.InlineKeyboardButton("Close chat", callback_data="contact_close"))
    return text, keyboard

def closed_chat_content():
    text = ("<b>Chat with the seller is closed.</b> Wait until the seller sees and responds to your messages.")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Reopen the chat", callback_data="contact_reopen"))
    keyboard.add(types.InlineKeyboardButton("History of dialogue", callback_data="contact_history"))
    keyboard.add(types.InlineKeyboardButton("🗑 Clear Chat", callback_data="contact_clear"))
    keyboard.add(types.InlineKeyboardButton("Menu", callback_data="main_back"))
    return text, keyboard

def chat_history_content(chat_id):
    history = db_get_chat_history(chat_id)
    if not history:
        text = "No chat History"
    else:
        text = "📋 <b>Dialogue history:</b>\n\n"
        for msg in history:
            ts = datetime.fromtimestamp(msg["ts"]).strftime("%d/%m %H:%M")
            if msg.get("from") == "seller":
                text += f"[{ts}] 🏪 <b>Seller:</b> {msg['text']}\n\n"
            else:
                text += f"[{ts}] 🧑 <b>You:</b> {msg['text']}\n\n"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Back", callback_data="contact_close"))
    return text, keyboard

# =========================
# CART SCREEN
# =========================

def cart_content(chat_id):
    cart = get_cart(chat_id)
    if not cart:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("Menu", callback_data="main_back"))
        return "🛒 Your cart is empty.", keyboard
    text = "Check your shopping basket, product list or prices have changed.\n\nTap an item to remove it from your basket."
    keyboard = types.InlineKeyboardMarkup()
    for i, item in enumerate(cart):
        unit = item.get("unit", "gr")
        label = f"{item['name']} {item['qty']:.2f} {unit} - £{item['price']:.2f}"
        keyboard.add(types.InlineKeyboardButton(label, callback_data=f"remove_{i}"))
    keyboard.add(types.InlineKeyboardButton("Checkout", callback_data="checkout", style="success"))
    keyboard.add(types.InlineKeyboardButton("Menu", callback_data="main_back"))
    return text, keyboard

# =========================
# CHECKOUT SCREEN
# =========================

def checkout_content(chat_id):
    cart = get_cart(chat_id)
    cart_total = get_cart_total(chat_id)
    discount = user_discount.get(chat_id)
    payment  = user_payment.get(chat_id)
    address  = user_address.get(chat_id)
    delivery = user_delivery.get(chat_id)
    invoice_num = user_pending_invoice.get(chat_id, "---")
    text = f"🧾 Invoice {invoice_num}\n\n"
    text += "Status: ⏰ Pending Checkout\n\n"
    text += ("Enter the discount code, payment method, address and delivery method. "
             "Once your order has been completed, you will be given payment the details\n\n")
    for i, item in enumerate(cart):
        unit = item.get("unit", "gr")
        text += f"{i+1}. {item['name']} {item['qty']:.2f} {unit} — £{item['price']:.2f}\n"
    delivery_cost = DELIVERY_COSTS.get(delivery, 0.0)
    discount_rate = DISCOUNT_CODES.get((discount or "").upper(), 0.0)
    discounted_subtotal = round(cart_total * (1 - discount_rate), 2)
    final_total = round(discounted_subtotal + delivery_cost, 2)
    text += f"\nTotal: £{final_total:.2f}"
    if discount:
        if discount.upper() in DISCOUNT_CODES:
            saved = round(cart_total * discount_rate, 2)
            text += f"\n🏷 Discount ({discount}): -£{saved:.2f} ({int(discount_rate*100)}% off)"
        else:
            text += f"\n🏷 Discount: {discount} ❌ Invalid code"
    if payment:  text += f"\n💳 Payment: {payment}"
    if address:  text += f"\n📦 Address: {address}"
    if delivery: text += f"\n🚚 Delivery: {delivery}"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton(f"Enter a discount code{' ✅' if discount else ''}", callback_data="checkout_discount"))
    keyboard.add(types.InlineKeyboardButton(f"Select Payment Method{' ✅' if payment else ''}", callback_data="checkout_payment"))
    keyboard.add(types.InlineKeyboardButton(f"Enter Delivery Address{' ✅' if address else ''}", callback_data="checkout_address"))
    keyboard.add(types.InlineKeyboardButton(f"Select Delivery Method{' ✅' if delivery else ''}", callback_data="checkout_delivery"))
    if payment and address and delivery:
        keyboard.add(types.InlineKeyboardButton(f"Checkout — £{final_total:.2f}", callback_data="checkout_confirm"))
    keyboard.row(
        types.InlineKeyboardButton("Delete Order", callback_data="delete_order"),
        types.InlineKeyboardButton("Back", callback_data="cart_main")
    )
    return text, keyboard

# =========================
# PAYMENT PENDING SCREEN
# =========================

def payment_pending_content(order):
    invoice        = order["invoice"]
    total          = order["total"]
    delivery       = order.get("delivery") or "Standard Delivery"
    items          = order["items"]
    status         = order["status"]
    crypto_amount  = order["crypto_amount"]
    crypto_symbol  = order["crypto_symbol"]
    crypto_address = order["crypto_address"]
    status_line    = format_status(order)
    delivery_cost  = DELIVERY_COSTS.get(delivery, 0.0)

    text  = f"🧾 Invoice {invoice}\n\n"
    if status == "expired":
        text += ("<b>Payment for the order has been cancelled.</b> If you have sent the coins but the order "
                 "is still cancelled, contact the seller with the order number and proof of payment.\n\n")
    else:
        text += f"Status: {status_line}\n\n"
        if crypto_address:
            text += f"<b>Payment Address:</b> <code>{crypto_address}</code>\n"
        if crypto_amount is not None and crypto_symbol:
            text += f"<b>Amount:</b> <code>{crypto_amount:.8f}</code> {crypto_symbol}\n\n"
            if status == "pending":
                text += (f"Please ensure that you send the exact <b>{crypto_symbol}</b> amount specified. "
                         f"It's important to note that certain exchanges or wallets may deduct fees "
                         f"from <b>your payment</b>, so kindly <b>double-check</b> the amount before sending your "
                         f"payment to make sure you sent the correct amount.\n\n")
    for i, item in enumerate(items):
        unit = item.get("unit", "gr")
        text += f"{i+1}. {item['name']} <code>{item['qty']:.2f}</code> {unit} — £{item['price']:.2f}\n"
    text += f"\n<b>Delivery:</b> {delivery} - £{delivery_cost:.2f}\n<b>Total:</b> £{total:.2f}"
    keyboard = types.InlineKeyboardMarkup()
    if status == "pending":
        keyboard.add(types.InlineKeyboardButton("Add Notes", callback_data=f"order_notes_{invoice}"))
        keyboard.add(types.InlineKeyboardButton("Refresh", callback_data=f"order_refresh_{invoice}"))
        if crypto_symbol != "XMR":
            keyboard.add(types.InlineKeyboardButton("📷 Show QR Code", callback_data=f"qr_{invoice}"))
    keyboard.add(types.InlineKeyboardButton("Back", callback_data="orders"))
    return text, keyboard

# =========================
# CHECKOUT SUB-SCREENS
# =========================

def payment_content():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Bitcoin", callback_data="pay_btc"))
    keyboard.add(types.InlineKeyboardButton("Litecoin", callback_data="pay_ltc"))
    keyboard.add(types.InlineKeyboardButton("Monero", callback_data="pay_xmr"))
    keyboard.add(types.InlineKeyboardButton("Back to Order", callback_data="checkout"))
    return "Choose your preferred payment method", keyboard

def address_content():
    text = ("You can send a message to the chat either as an encrypted message or as plain text. "
            "The bot will handle the encryption of your message and display it to the seller after the order is paid for.\n\n"
            "Please type your address in this format:\n\n"
            "<code>(YOUR NAME) - JAMES HILLS\n"
            "(STREET NAME + NUMBER) - Victoria St 155\n"
            "(CITY) - LONDON\n"
            "(POSTAL CODE) - SW1E 5N\n"
            "(COUNTRY) - UNITED KINGDOM</code>")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Why is it safe?", callback_data="checkout_why_safe"))
    keyboard.add(types.InlineKeyboardButton("Vendor PGP Key", callback_data="checkout_pgp_key"))
    keyboard.add(types.InlineKeyboardButton("Cancel", callback_data="checkout"))
    return text, keyboard

def delivery_content():
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Tracked24 - £5.00", callback_data="del_tracked24"))
    keyboard.add(types.InlineKeyboardButton("Special Delivery Guaranteed by 1PM - £10.00",
                                            callback_data="del_special_delivery"))
    keyboard.add(types.InlineKeyboardButton("Back to Order", callback_data="checkout"))
    return "Please select a delivery method", keyboard

def discount_content():
    text = ("If you have a discount code you can enter it here.\n\n"
            "If you don't, clicking Cancel will take you back to the order.")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Cancel", callback_data="checkout"))
    return text, keyboard

# =========================
# ORDERS SCREEN
# =========================

def orders_content(chat_id):
    db_mark_expired()
    rows = db_get_orders(chat_id)
    if not rows:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(" Back", callback_data="main_back"))
        return "📦 You have no orders yet.", keyboard
    status_icons = {"pending": "🕐", "expired": "❌", "completed": "✅"}
    text = "📦 Your Orders:\n"
    keyboard = types.InlineKeyboardMarkup()
    for row in rows:
        invoice, total, payment, status, created_at, expires_at, completed_at = row
        icon = status_icons.get(status, "•")
        label = f"{icon} Invoice #{invoice} — £{total} — {status.capitalize()}"
        text += f"\n{label}"
        keyboard.add(types.InlineKeyboardButton(label, callback_data=f"order_{invoice}"))
    keyboard.add(types.InlineKeyboardButton(" Back", callback_data="main_back"))
    return text, keyboard

def order_detail_content(chat_id, invoice_num):
    db_mark_expired()
    order = db_get_order(invoice_num)
    if not order or order["chat_id"] != chat_id:
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton(" Back", callback_data="orders"))
        return "❌ Order not found.", keyboard
    return payment_pending_content(order)

# =========================
# MAIN MENU
# =========================

def about_content():
    text = ("✅<b>About</b>\n\n"
            "When you choose McWhite's, you can count on:\n"
            "🍀 Reliable Service: Your orders will always be dispatched on time.\n"
            "🍀 Discreet Delivery: Exceptional stealth packaging.\n"
            "🍀 Tracked Delivery: Royal Mail T24\n"
            "🍀 Uncompromising Quality: We take pride in offering only the best.\n\n"
            "At McWhite's, orders placed Monday through Friday before the 12.30 pm will be dispatched "
            "the same day. Orders placed after 12.30pm or during the weekend will be processed and "
            "dispatched on the next business day.\n\n"
            "Check for updates, sometimes we post on Saturdays :)")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Back to Main menu", callback_data="main_back"))
    return text, keyboard

def terms_content():
    text = ("✅<b>Terms &amp; Conditions</b>\n\n"
            "#\t Monday to Friday 12.30pm same day dispatch.\n\n"
            "Terms &amp; Conditions\n\n"
            "Kindly review before placing an order:\n\n"
            "1. Shipping &amp; Tracking\n"
            "• Tracked 24: Tracking information is available 24 hours after dispatch.\n\n"
            "2. Reship Guidelines\n"
            "•  Parcels qualify for a 50% reship if not delivered within 10 working days from the shipping date.\n\n"
            "3. Payment Requirements\n"
            "• Send the exact total, including all transfer fees.\n"
            "• Payments must be sent from a private/external wallet — do not send directly from exchanges.\n\n"
            "4. Delivery Information\n"
            "• Customers must provide complete and accurate shipping details.\n"
            "• We are not responsible for issues caused by incorrect or missing address information.\n\n"
            "5. Out of Stock Items\n"
            "• If by any chance a product is unavailable, I will try to contact you and ask for alternative, "
            "but it may be replaced with a similar item within the same price range by me.\n\n"
            "7. Claims for Missing or Underweight Items\n"
            "• A single, clear, uninterrupted unboxing video is required showing:\n"
            "• The sealed package prior to opening\n"
            "• All contents and visible weight measurements\n"
            "• Claims submitted without this video will not be approved.")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Back to Main menu", callback_data="main_back"))
    return text, keyboard

# =========================
# FAQ
# =========================

FAQ_QUESTIONS = [
    ("faq_delivery", "\U0001F69A How long will my delivery take to arrive?"),
    ("faq_address",  "\U0001F4EE How do I send my address?"),
    ("faq_payment",  "\U0001F4B3 What payment methods do you accept?"),
    ("faq_tracking", "\U0001F4EE How do I get my tracking number?"),
    ("faq_sdt24",    "\U0001F4EE SD or T24 has not moved or scanned in"),
]

FAQ_ANSWERS = {
    "faq_delivery": (
        "⏰ Delivery times for your order vary depending on the shipping method and your location. "
        "Once your order is marked as dispatched, please allow the following timeframes:\n\n"
        "\U0001F4EC Track24: Usually arrives within 1-3 business days but may take up to 5 business days\n\n"
        "\U0001F4EC Special Delivery: Typically delivered within 1-2 business days\n\n"
        "Please note that these timeframes are approximate and may be subject to variations due to "
        "local postal services."
    ),
    "faq_address": (
        "We strongly encourge you to use PGP encryption when sending your address details as it is "
        "the safest way of doing so. Otherwise, please use a temp.pm not set to expire within 5 DAYS."
    ),
    "faq_payment": "We accept crypto only, BTC, LTC &amp; XMR",
    "faq_tracking": (
        "Once your order is completed on @SirMono_trackbot\n\n"
        "• Use our Trackbot: @SirMono_trackbot\n\n"
        "OR\n\n"
        "•\n"
        "On the bot, hit the contact button, this has now started a chat.\n\n"
        "•\n"
        "Ask for tracking and then type in this format exactly:\n\n"
        "➡️ Your Name\n"
        "➡️ Your Order ID\n"
        "➡️ Your Postcode\n\n"
        "Note: We suggest using PGP encryption when sending your personal information.\n\n"
        "•\n"
        "Click send/enter and then make sure to close the chat. This then sends your message.\n\n"
        "•\n"
        "Wait patiently for our support team to respond to your query. We strive to respond to all "
        "messages fairly quickly and maintain a focus on customer support.\n\n"
        "OR\n\n"
        "•\n"
        "Message our support team who you can find in our groupchat bio\n"
        "https://t.me/+Rfzp0SVqzsFiOWQ0\n\n"
        "•\n"
        "Ask for tracking and then type in this format exactly:\n\n"
        "➡️ Your Name\n"
        "➡️ Your Order ID\n"
        "➡️ Your Postcode\n\n"
        "Note: We suggest using PGP encryption when sending your personal information."
    ),
    "faq_sdt24": (
        "If in any case your order doesnt move or get scanned in within 10 days you are entitled to a "
        "50% reship (ONCE) as RM does not consider your mail lost until after 10 days. Message the bot "
        "or our support team explaining your issue and we will reship your order."
    ),
}

def faq_content():
    text = "Select your question to see the answer"
    keyboard = types.InlineKeyboardMarkup()
    for key, label in FAQ_QUESTIONS:
        keyboard.add(types.InlineKeyboardButton(label, callback_data=key))
    keyboard.add(types.InlineKeyboardButton("Back to Main Menu", callback_data="main_back"))
    return text, keyboard

def faq_answer_content(key):
    label = dict(FAQ_QUESTIONS)[key]
    text = f"<b>{label}</b>\n\n{FAQ_ANSWERS[key]}"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Back to FAQ", callback_data="faq"))
    return text, keyboard

def main_menu_content(chat_id=None):
    caption = ("Last seen: recently\n"
               "Ships from: 🇬🇧 → 🇬🇧\n"
               "Sales: 23,313\n"
               "Currency: GBP\n"
               "Rating: ★4.94 (7,801)\n"
               "Welcome to SirMonopoly\n"
               "The KING of NDD\n\n"
               "Known for the best and most consistent quality across the board\n\n"
               "🏆 The Best Gear\n\n"
               "🏆 The Best Prices\n\n"
               "⏰ Same-day Dispatch ⏰\n"
               "Mon - Fri: 3pm\n\n"
               "📱 Support & Links 📱\n"
               "https://linktr.ee/SirMonopoly\n"
               "https://t.me/+C8dZ6apSIKZkY2I0\n\n"
               "BOOKMARK VENDOR PROFILES\n"
               "https://tsbots.to/v/\n"
               "https://tsbots.eth.limo")
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("🚀 Listings", callback_data="listings"))
    keyboard.add(types.InlineKeyboardButton("⚠️ READ BEFORE ORDERING ⚠️", callback_data="terms"))
    keyboard.add(types.InlineKeyboardButton("ℹ️ About ℹ️", callback_data="about"))
    keyboard.row(
        types.InlineKeyboardButton("❓ FAQ", callback_data="faq"),
        types.InlineKeyboardButton("Terms of Service", callback_data="terms")
    )
    keyboard.row(
        types.InlineKeyboardButton("📊 Rating ★4.96", callback_data="reviews_page1"),
        types.InlineKeyboardButton("🔑 PGP", callback_data="pgp")
    )
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    order_count = len(db_get_orders(chat_id)) if chat_id else 0
    order_label = f"Orders ({order_count})" if order_count else "Orders"
    wl_count = len(db_get_wishlist(chat_id)) if chat_id else 0
    wl_label = f"👤 Profile ({wl_count})" if wl_count else "👤 Profile"
    keyboard.row(
        types.InlineKeyboardButton(order_label, callback_data="orders"),
        types.InlineKeyboardButton(wl_label, callback_data="wishlist"),
        types.InlineKeyboardButton(cl, callback_data="cart_main")
    )
    keyboard.add(types.InlineKeyboardButton("📬 Contact", callback_data="contact"))
    return caption, keyboard

# =========================
# CATEGORY / SUB MENUS
# =========================

def category_menu_content(chat_id=None):
    caption = "Select a product category to fill your shopping basket"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("🇬🇧 UNITED KINGDOM 🇬🇧 (39)", callback_data="uk_listings"))
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    keyboard.row(
        types.InlineKeyboardButton("Back", callback_data="main_back"),
        types.InlineKeyboardButton("Price List", callback_data="price_list"),
        types.InlineKeyboardButton(cl, callback_data="cart_main")
    )
    return caption, keyboard

def uk_listings_content(chat_id=None):
    caption = "Category: 🇬🇧UNITED KINGDOM🇬🇧"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("🌻 UK grown flowers 🌻 (11)", callback_data="cat_uk_flowers"))
    keyboard.add(types.InlineKeyboardButton("🎒 Budget smoke 🎒 (4)", callback_data="cat_budget"))
    keyboard.add(types.InlineKeyboardButton("🍫 Hash 🍫 (5)", callback_data="cat_hash"))
    keyboard.add(types.InlineKeyboardButton("🔥 BUNDLES (4)", callback_data="cat_bundles"))
    keyboard.add(types.InlineKeyboardButton("🇺🇸⭐ CALI (5)", callback_data="cat_cali"))
    keyboard.add(types.InlineKeyboardButton("🍯 EXTRACTS 🍯 (4)", callback_data="cat_extracts"))
    keyboard.add(types.InlineKeyboardButton("🇨🇦 IMPORTS 🇨🇦 (1)", callback_data="cat_imports"))
    keyboard.add(types.InlineKeyboardButton("📦 BULK OFFERS (1)", callback_data="cat_bulk"))
    keyboard.add(types.InlineKeyboardButton("⭐ OFFER ⭐ (3)", callback_data="cat_offers"))
    keyboard.add(types.InlineKeyboardButton("☁️ VAPES ☁️ (1)", callback_data="cat_vapes"))
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    keyboard.row(
        types.InlineKeyboardButton("Back", callback_data="listings"),
        types.InlineKeyboardButton(cl, callback_data="cart_main")
    )
    return caption, keyboard

def uk_flowers_page1_content(chat_id=None):
    caption = "Category: 🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("🇬🇧 Skywalker OG", callback_data="flower_skywalker"))
    keyboard.add(types.InlineKeyboardButton("🇬🇧 RUNTZ", callback_data="flower_runtz"))
    keyboard.add(types.InlineKeyboardButton("🇬🇧⭐ CRIMINAL+", callback_data="flower_criminal"))
    keyboard.add(types.InlineKeyboardButton("🇬🇧⭐ McCAKE🇬🇧", callback_data="flower_mccake"))
    keyboard.add(types.InlineKeyboardButton("🇬🇧 STARDAWG", callback_data="flower_stardawg"))
    keyboard.add(types.InlineKeyboardButton("🇬🇧 CHERPAYA", callback_data="flower_cherpaya"))
    keyboard.add(types.InlineKeyboardButton("🇬🇧 Amnesia Haze", callback_data="flower_amnesia"))
    keyboard.add(types.InlineKeyboardButton("🇬🇧🍒 CHERRY LEMONADE 🍒🇬🇧", callback_data="flower_cherry"))
    keyboard.add(types.InlineKeyboardButton("🇬🇧🆕 Gelato🇬🇧", callback_data="flower_gelato"))
    keyboard.add(types.InlineKeyboardButton("🇬🇧🍦 Cream 47", callback_data="flower_cream47"))
    keyboard.row(
        types.InlineKeyboardButton("1 ✦", callback_data="uk_flowers_p1"),
        types.InlineKeyboardButton("2", callback_data="uk_flowers_p2")
    )
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    keyboard.row(
        types.InlineKeyboardButton("Back", callback_data="uk_listings"),
        types.InlineKeyboardButton(cl, callback_data="cart_main")
    )
    return caption, keyboard

def uk_flowers_page2_content(chat_id=None):
    caption = "Category: 🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("🇬🇧 Godfather OG 🇬🇧", callback_data="flower_godfather"))
    keyboard.add(types.InlineKeyboardButton("🇬🇧 Frosted Lemon", callback_data="flower_frosted_lemon"))
    keyboard.add(types.InlineKeyboardButton("🇬🇧 Wedding Cake", callback_data="flower_wedding_cake"))
    keyboard.row(
        types.InlineKeyboardButton("1", callback_data="uk_flowers_p1"),
        types.InlineKeyboardButton("2 ✦", callback_data="uk_flowers_p2")
    )
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    keyboard.row(
        types.InlineKeyboardButton("Back", callback_data="uk_listings"),
        types.InlineKeyboardButton(cl, callback_data="cart_main")
    )
    return caption, keyboard

def budget_smoke_content(chat_id=None):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🍀 BLUEBERRY SUGAR SMALLS 40G", callback_data="p_bss40"))
    kb.add(types.InlineKeyboardButton("🍀 MIMOSA SMALLS 25G", callback_data="p_mim25"))
    kb.add(types.InlineKeyboardButton("🍀 STARDAWG SMALLS", callback_data="p_stardawg_sm"))
    kb.add(types.InlineKeyboardButton("🇬🇧 🌟 SHAKE", callback_data="p_shake"))
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    kb.row(types.InlineKeyboardButton("Back", callback_data="uk_listings"),
           types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return "Category: 🇬🇧UNITED KINGDOM🇬🇧 → 🎒 Budget smoke 🎒", kb

def hash_content(chat_id=None):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💣 TEMPLE BALLS", callback_data="p_temple"))
    kb.add(types.InlineKeyboardButton("🇬🇧 KETAMA", callback_data="p_ketama"))
    kb.add(types.InlineKeyboardButton("✨ 🇬🇧 CHE GUEVARA", callback_data="p_che"))
    kb.add(types.InlineKeyboardButton("✨ 🇬🇧 DRAGO", callback_data="p_drago"))
    kb.add(types.InlineKeyboardButton("✨ 🇬🇧 DONKEY KONG", callback_data="p_donkey"))
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    kb.row(types.InlineKeyboardButton("Back", callback_data="uk_listings"),
           types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return "Category: 🇬🇧UNITED KINGDOM🇬🇧 → 🍫 Hash 🍫", kb

def bundles_content(chat_id=None):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💎 20g Hash Bundle – 80GBP 💎", callback_data="p_hash_bundle"))
    kb.add(types.InlineKeyboardButton("🔥 Mix & Match CALI bundle 🔥", callback_data="p_mixmatch"))
    kb.add(types.InlineKeyboardButton("🚀 3UK strains for £115 – 21g Deal 🚀", callback_data="p_3uk"))
    kb.add(types.InlineKeyboardButton("🔥🔥 BUDGET SURVIVAL BOX 🔥🔥", callback_data="p_survbox"))
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    kb.row(types.InlineKeyboardButton("Back", callback_data="uk_listings"),
           types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return "Category: 🇬🇧UNITED KINGDOM🇬🇧 → 🔥BUNDLES", kb

def cali_content(chat_id=None):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🇺🇸⭐ Sour RUNTZ", callback_data="p_sour_runtz"))
    kb.add(types.InlineKeyboardButton("🇺🇸⭐ Fire Runtz", callback_data="p_fire_runtz"))
    kb.add(types.InlineKeyboardButton("🇺🇸⭐ Dosi Cakes", callback_data="p_dosi"))
    kb.add(types.InlineKeyboardButton("🇺🇸⭐ Ice Age", callback_data="p_iceage"))
    kb.add(types.InlineKeyboardButton("🇺🇸⭐ Twix Gushers", callback_data="p_twix"))
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    kb.row(types.InlineKeyboardButton("Back", callback_data="uk_listings"),
           types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return "Category: 🇬🇧UNITED KINGDOM🇬🇧 → 🇺🇸⭐ CALI", kb

def extracts_content(chat_id=None):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🇺🇸 Havana Nights", callback_data="p_havana"))
    kb.add(types.InlineKeyboardButton("🇺🇸 Gelato", callback_data="p_gelato_ext"))
    kb.add(types.InlineKeyboardButton("🇺🇸 AirHeadz", callback_data="p_airheadz"))
    kb.add(types.InlineKeyboardButton("🇺🇸 Mochi Gelato", callback_data="p_mochi"))
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    kb.row(types.InlineKeyboardButton("Back", callback_data="uk_listings"),
           types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return "Category: 🇬🇧UNITED KINGDOM🇬🇧 → 🍯EXTRACTS🍯", kb

def imports_content(chat_id=None):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⭐🇨🇦 ICE CREAM CAKE", callback_data="p_icecream"))
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    kb.row(types.InlineKeyboardButton("Back", callback_data="uk_listings"),
           types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return "Category: 🇬🇧UNITED KINGDOM🇬🇧 → 🇨🇦IMPORTS🇨🇦", kb

def bulk_offers_content(chat_id=None):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("1 BAR HASH 100G", callback_data="p_bar_hash"))
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    kb.row(types.InlineKeyboardButton("Back", callback_data="uk_listings"),
           types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return "Category: 🇬🇧UNITED KINGDOM🇬🇧 → 📦BULK OFFERS", kb

def offers_content(chat_id=None):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔥 STARDAWG", callback_data="p_offer_stardawg"))
    kb.add(types.InlineKeyboardButton("🔥 GELATO41 🔥", callback_data="p_offer_gelato41"))
    kb.add(types.InlineKeyboardButton("🔥 BLUE NERDZ 🔥", callback_data="p_offer_bluenerdz"))
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    kb.row(types.InlineKeyboardButton("Back", callback_data="uk_listings"),
           types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return "Category: 🇬🇧UNITED KINGDOM🇬🇧 → ⭐OFFER⭐", kb

def vapes_content(chat_id=None):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("☁️ 1g Disposable vapes ☁️", callback_data="p_vape1g"))
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    kb.row(types.InlineKeyboardButton("Back", callback_data="uk_listings"),
           types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return "Category: 🇬🇧UNITED KINGDOM🇬🇧 → ☁️VAPES☁️", kb

def dissconnectors_content(chat_id=None):
    caption = "Current category: Disconector"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Ketchup (3)", callback_data="kettle_menu"))
    cl = cart_button(chat_id) if chat_id else "🛒 Cart"
    keyboard.row(types.InlineKeyboardButton(" Back", callback_data="cat_back"), types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return caption, keyboard

def kettle_menu_content(chat_id=None):
    caption = "Current category: Ketchup"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Ketchup", callback_data="kettle_10"))
    keyboard.add(types.InlineKeyboardButton("Indian Ketchup", callback_data="kettle_indian"))
    keyboard.add(types.InlineKeyboardButton("Dutch Ketchup", callback_data="kettle_dutch"))
    cl = cart_button(chat_id) if chat_id else "🛒 Cart"
    keyboard.row(types.InlineKeyboardButton(" Back", callback_data="cat1"), types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return caption, keyboard

def egg_content(chat_id=None):
    caption = "Current category: Eclair"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("Mdaffara", callback_data="eggwhite"))
    keyboard.add(types.InlineKeyboardButton("egg", callback_data="egg_black"))
    cl = cart_button(chat_id) if chat_id else "🛒 Cart"
    keyboard.row(types.InlineKeyboardButton(" Back", callback_data="cat_back"), types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return caption, keyboard

def peanut_menu_content(chat_id=None):
    caption = "Current category: Psyllium"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("2psyllium (2)", callback_data="2peanut"))
    keyboard.add(types.InlineKeyboardButton("dpsyllium (1)", callback_data="dpeanut"))
    keyboard.add(types.InlineKeyboardButton("Mpsylium (2)", callback_data="mpeanut"))
    cl = cart_button(chat_id) if chat_id else "🛒 Cart"
    keyboard.row(types.InlineKeyboardButton(" Back", callback_data="cat_back"), types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return caption, keyboard

def brownpeanut_content(chat_id=None):
    caption = "Current category: 2psyllium"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("2psylliumg 25", callback_data="brownpeanut_g"))
    keyboard.add(types.InlineKeyboardButton("2psyllium 22", callback_data="brownpeanut_p"))
    cl = cart_button(chat_id) if chat_id else "🛒 Cart"
    keyboard.row(types.InlineKeyboardButton(" Back", callback_data="cat4"), types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return caption, keyboard

def mpeanut_content(chat_id=None):
    caption = "Current category: M psylium"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("M psylium5G", callback_data="mpeanut_c"))
    keyboard.add(types.InlineKeyboardButton("AM psylium", callback_data="apeanut_s"))
    cl = cart_button(chat_id) if chat_id else "🛒 Cart"
    keyboard.row(types.InlineKeyboardButton(" Back", callback_data="cat4"), types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return caption, keyboard

def sugar_content(chat_id=None):
    caption = "Current category: Stilton"
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("AStilton (1)", callback_data="Stilton_menu"))
    keyboard.add(types.InlineKeyboardButton("CStilton (1)", callback_data="cok"))
    keyboard.add(types.InlineKeyboardButton("MStilton (1)", callback_data="mk"))
    cl = cart_button(chat_id) if chat_id else "🛒 Cart"
    keyboard.row(types.InlineKeyboardButton(" Back", callback_data="cat_back"), types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return caption, keyboard

def wishlist_content(chat_id=None):
    wl = db_get_wishlist(chat_id) if chat_id else set()
    if not wl:
        caption = "❤️ Your Wishlist\n\nYour wishlist is empty."
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("Back", callback_data="main_back"))
        return caption, keyboard
    caption = f"❤️ Your Wishlist ({len(wl)} items)\n\nTap a product to view it:"
    keyboard = types.InlineKeyboardMarkup()
    for prefix in wl:
        if prefix in PRODUCTS:
            _, _, _, name = PRODUCTS[prefix]
            keyboard.add(types.InlineKeyboardButton(name, callback_data=f"wl_open_{prefix}"))
    keyboard.add(types.InlineKeyboardButton("Back", callback_data="main_back"))
    return caption, keyboard

# =========================
# PRODUCT CONTENT FUNCTIONS
# Third element is image key (str) or None
# =========================

def kettle_10_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["kettle_10"](qty)
    text = (f"🧪 <b>Ketchup</b>\n\n"
            f"Top quality product.\n\n"
            f"Quantity: <b>{qty}g</b>\nPrice: <b>£{price}</b>")
    keyboard = build_product_keyboard("kettle_10", "kettle_menu", 0.5, qty, price, chat_id)
    return text, keyboard, None

def indian_kettle_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["kettle_indian"](qty)
    text = (f"🧪 <b>Indian Ketchup</b>\n\n"
            f"Top quality product.\n\n"
            f"Quantity: <b>{qty}g</b>\nPrice: <b>£{price}</b>")
    keyboard = build_product_keyboard("kettle_indian", "kettle_menu", 0.5, qty, price, chat_id)
    return text, keyboard, None

def dutch_kettle_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 14.0
    price = PRICE_FNS["kettle_dutch"](qty)
    text = (f"🧪 <b>Dutch Ketchup</b>\n\n"
            f"Top quality product.\n\n"
            f"Quantity: <b>{qty}g</b>\nPrice: <b>£{price}</b>")
    keyboard = build_product_keyboard("kettle_dutch", "kettle_menu", 3.5, qty, price, chat_id)
    return text, keyboard, None

def eggwhite_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["eggwhite"](qty)
    text = (f"🧪 <b>Mdaffara</b>\n\n"
            f"Top quality product.\n\n"
            f"Quantity: <b>{qty}g</b>\nPrice: <b>£{price}</b>")
    keyboard = build_product_keyboard("eggwhite", "cat2", 0.5, qty, price, chat_id)
    return text, keyboard, None

def egg_black_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 10.0
    price = PRICE_FNS["egg_black"](qty)
    text = (f"🧪 <b>egg</b>\n\n"
            f"Top quality product.\n\n"
            f"Quantity: <b>{qty}g</b>\nPrice: <b>£{price}</b>")
    keyboard = build_product_keyboard("egg_black", "cat2", 1.0, qty, price, chat_id)
    return text, keyboard, None

def brownpeanut_g_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 25.0
    price = PRICE_FNS["brownpeanut_g"](qty)
    text = (f"🧪 <b>2psylliumg 25</b>\n\n"
            f"Top quality product.\n\n"
            f"Quantity: <b>{qty}g</b>\nPrice: <b>£{price}</b>")
    keyboard = build_product_keyboard("brownpeanut_g", "2peanut", 5.0, qty, price, chat_id)
    return text, keyboard, None

def brownpeanut_p_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 10.0
    price = PRICE_FNS["brownpeanut_p"](qty)
    text = (f"🧪 <b>2psyllium 22</b>\n\n"
            f"Top quality product.\n\n"
            f"Quantity: <b>{qty}g</b>\nPrice: <b>£{price}</b>")
    keyboard = build_product_keyboard("brownpeanut_p", "2peanut", 1.0, qty, price, chat_id)
    return text, keyboard, None

def dpeanut_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 1.0
    price = PRICE_FNS["dpeanut"](qty)
    text = (f"🧪 <b>dpsyllium</b>\n\n"
            f"Top quality product.\n\n"
            f"Quantity: <b>{qty}g</b>\nPrice: <b>£{price}</b>")
    keyboard = build_product_keyboard("dpeanut", "cat4", 0.25, qty, price, chat_id)
    return text, keyboard, None

def mpeanut_c_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 2.0
    price = PRICE_FNS["mpeanut_c"](qty)
    text = (f"🧪 <b>M psylium5G</b>\n\n"
            f"Top quality product.\n\n"
            f"Quantity: <b>{qty}g</b>\nPrice: <b>£{price}</b>")
    keyboard = build_product_keyboard("mpeanut_c", "mpeanut", 0.5, qty, price, chat_id)
    return text, keyboard, None

def apeanut_s_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 14.0
    price = PRICE_FNS["apeanut_s"](qty)
    text = (f"🧪 <b>AM psylium</b>\n\n"
            f"Top quality product.\n\n"
            f"Quantity: <b>{qty}g</b>\nPrice: <b>£{price}</b>")
    keyboard = build_product_keyboard("apeanut_s", "mpeanut", 3.5, qty, price, chat_id)
    return text, keyboard, None

def speed_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 7.0
    price = PRICE_FNS["speed_menu"](qty)
    text = (f"🧪 <b>AStilton</b>\n\n"
            f"Top quality product.\n\n"
            f"Quantity: <b>{qty}g</b>\nPrice: <b>£{price}</b>")
    keyboard = build_product_keyboard("speed_menu", "cat5", 3.5, qty, price, chat_id)
    return text, keyboard, None

def coke_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 1.75
    price = PRICE_FNS["cok"](qty)
    text = (f"🧪 <b>CStilton</b>\n\n"
            f"Top quality product.\n\n"
            f"Quantity: <b>{qty}g</b>\nPrice: <b>£{price}</b>")
    keyboard = build_product_keyboard("cok", "cat5", 0.25, qty, price, chat_id)
    return text, keyboard, None

def mkat_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["mk"](qty)
    text = (f"🧪 <b>MStilton</b>\n\n"
            f"Top quality product.\n\n"
            f"Quantity: <b>{qty}g</b>\nPrice: <b>£{price}</b>")
    keyboard = build_product_keyboard("mk", "cat5", 0.5, qty, price, chat_id)
    return text, keyboard, None

# =========================
# MCW PRODUCT CONTENT FUNCTIONS
# =========================

def p_offer_stardawg_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 10.0
    price = PRICE_FNS["p_offer_stardawg"](qty)
    text = ("🔥<b>STARDAWG</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → ⭐OFFER⭐ • Stock 3,500.00 • ★ 0.0 (0)\n\n"
            "ITS NOT TOP QUALITY, but great smoke for price\n\n"
            "Stardawg is a legendary hybrid known for its pungent diesel aroma, earthy pine notes, "
            "and a sharp citrus kick. It's typically more energizing and cerebral than many dessert "
            "or indica-heavy strains, making it a popular daytime or social smoke.\n\n"
            "from <b>10.00</b> gr @ £4.90\n"
            "from <b>20.00</b> gr @ £4.70\n"
            "from <b>30.00</b> gr @ £4.50\n"
            "from <b>50.00</b> gr @ £4.30\n"
            "from <b>100.00</b> gr @ £4.00\n"
            "from <b>200.00</b> gr @ £3.80")
    keyboard = build_mcw_keyboard("p_offer_stardawg", "cat_offers", 10.0, qty, price, chat_id, reviews=0)
    return text, keyboard, None

def p_offer_gelato41_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 10.0
    price = PRICE_FNS["p_offer_gelato41"](qty)
    text = ("🔥<b>GELATO41</b>🔥\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → ⭐OFFER⭐ • Stock 1,290.00 • ★ 4.9 (16)\n\n"
            "This is not top quality smoke, but could not be better for the price. Cali imports\n\n"
            "Gelato 41 is a heavy-hitting hybrid known for its creamy dessert flavor, sweet berry notes, "
            "and smooth body relaxation.\n\n"
            "from <b>10.00</b> gr @ £5.50\n"
            "from <b>20.00</b> gr @ £5.20\n"
            "from <b>30.00</b> gr @ £4.70\n"
            "from <b>50.00</b> gr @ £4.40")
    keyboard = build_mcw_keyboard("p_offer_gelato41", "cat_offers", 10.0, qty, price, chat_id, reviews=16)
    return text, keyboard, None

def p_offer_bluenerdz_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 10.0
    price = PRICE_FNS["p_offer_bluenerdz"](qty)
    text = ("🔥<b>BLUE NERDZ</b>🔥\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → ⭐OFFER⭐ • Stock 275.00 • ★ 4.8 (13)\n\n"
            "This is not top quality smoke, may have some seeds, but could not be better for the price. Cali imports\n\n"
            "Blue Nerdz is a sweet candy-style hybrid with strong blueberry, citrus, and gassy flavors. "
            "The high is usually balanced — starts euphoric and uplifting, then eases into a relaxed body buzz "
            "without knocking you out. Great for chilling, music, gaming, or social vibes.\n\n"
            "from <b>10.00</b> gr @ £5.50\n"
            "from <b>20.00</b> gr @ £5.20\n"
            "from <b>30.00</b> gr @ £4.70\n"
            "from <b>50.00</b> gr @ £4.40\n"
            "from <b>200.00</b> gr @ £4.10")
    keyboard = build_mcw_keyboard("p_offer_bluenerdz", "cat_offers", 10.0, qty, price, chat_id, reviews=13)
    return text, keyboard, None

def p_bar_hash_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 1.0
    price = PRICE_FNS["p_bar_hash"](qty)
    text = ("🍫<b>1 BAR HASH 100G</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 📦BULK OFFERS • Stock 28.00 • ★ 0.0 (0)\n\n"
            "aprox 100g per bar . great hash for the price\n\n"
            "pls check reviews on small weights listing :)\n\n"
            "DRAGO\nCHEGUEVARA\nDONKEY KONG\n\n"
            "from <b>1.00</b> pcs @ £310.00\n"
            "from <b>2.00</b> pcs @ £295.00\n"
            "from <b>3.00</b> pcs @ £285.00\n"
            "from <b>5.00</b> pcs @ £270.00")
    keyboard = build_mcw_keyboard("p_bar_hash", "cat_bulk", 1.0, qty, price, chat_id, reviews=0, unit="pcs")
    return text, keyboard, None

def p_icecream_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["p_icecream"](qty)
    text = ("⭐🇨🇦<b>ICE CREAM CAKE</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🇨🇦IMPORTS🇨🇦 • Stock 480.00 • ★ 5.0 (9)\n\n"
            "🇨🇦Canada imports🇨🇦 posted from UK.\n\n"
            "Ice Cream Cake\n"
            "Indica-leaning hybrid (Wedding Cake × Gelato #33) with a sweet vanilla / creamy dessert flavor. "
            "Gives a euphoric start then a deep, sleepy body high—more on the heavy side, best for night.\n"
            "Good for stress, pain, sleep.\n\n"
            "from <b>3.50</b> gr @ £9.50\n"
            "from <b>7.00</b> gr @ £8.33\n"
            "from <b>14.00</b> gr @ £7.40\n"
            "from <b>28.00</b> gr @ £6.80\n"
            "from <b>56.00</b> gr @ £6.20\n"
            "from <b>112.00</b> gr @ £5.80\n"
            "from <b>250.00</b> gr @ £5.40")
    keyboard = build_mcw_keyboard("p_icecream", "cat_imports", 3.5, qty, price, chat_id, reviews=9)
    return text, keyboard, None

def p_havana_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 1.0
    price = PRICE_FNS["p_havana"](qty)
    text = ("🇺🇸<b>Havana Nights</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🍯EXTRACTS🍯 • Stock 26.00 • ★ 5.0 (9)\n\n"
            "🇺🇸Havana nights crumble. USA import. strong and terpy.\n\n"
            "from <b>1.00</b> gr @ £24.00\n"
            "from <b>2.00</b> gr @ £22.00\n"
            "from <b>5.00</b> gr @ £18.00\n"
            "from <b>10.00</b> gr @ £17.00\n"
            "from <b>20.00</b> gr @ £16.00\n"
            "from <b>50.00</b> gr @ £14.50")
    keyboard = build_mcw_keyboard("p_havana", "cat_extracts", 1.0, qty, price, chat_id, reviews=9)
    return text, keyboard, "p_havana"

def p_gelato_ext_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 1.0
    price = PRICE_FNS["p_gelato_ext"](qty)
    text = ("🇺🇸<b>Gelato</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🍯EXTRACTS🍯 • Stock 26.00 • ★ 5.0 (6)\n\n"
            "🇺🇸Gelato crumble. USA import. strong and terpy.\n\n"
            "from <b>1.00</b> gr @ £24.00\n"
            "from <b>2.00</b> gr @ £22.00\n"
            "from <b>5.00</b> gr @ £18.00\n"
            "from <b>10.00</b> gr @ £17.00\n"
            "from <b>20.00</b> gr @ £16.00\n"
            "from <b>50.00</b> gr @ £14.50")
    keyboard = build_mcw_keyboard("p_gelato_ext", "cat_extracts", 1.0, qty, price, chat_id, reviews=6)
    return text, keyboard, "p_gelato_ext"

def p_airheadz_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 1.0
    price = PRICE_FNS["p_airheadz"](qty)
    text = ("🇺🇸<b>AirHeadz</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🍯EXTRACTS🍯 • Stock 51.00 • ★ 5.0 (11)\n\n"
            "🇺🇸Airheadz crumble. USA import. strong and terpy.\n\n"
            "from <b>1.00</b> gr @ £24.00\n"
            "from <b>2.00</b> gr @ £22.00\n"
            "from <b>5.00</b> gr @ £18.00\n"
            "from <b>10.00</b> gr @ £17.00\n"
            "from <b>20.00</b> gr @ £16.00\n"
            "from <b>50.00</b> gr @ £14.50")
    keyboard = build_mcw_keyboard("p_airheadz", "cat_extracts", 1.0, qty, price, chat_id, reviews=11)
    return text, keyboard, "p_airheadz"

def p_mochi_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 1.0
    price = PRICE_FNS["p_mochi"](qty)
    text = ("🇺🇸<b>Mochi Gelato</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🍯EXTRACTS🍯 • Stock 51.50 • ★ 5.0 (6)\n\n"
            "🇺🇸Mochi Gelato sugar wax. USA import. strong and terpy.\n\n"
            "from <b>1.00</b> gr @ £24.00\n"
            "from <b>2.00</b> gr @ £22.00\n"
            "from <b>5.00</b> gr @ £18.00\n"
            "from <b>10.00</b> gr @ £17.00\n"
            "from <b>20.00</b> gr @ £16.00\n"
            "from <b>50.00</b> gr @ £14.50")
    keyboard = build_mcw_keyboard("p_mochi", "cat_extracts", 1.0, qty, price, chat_id, reviews=6)
    return text, keyboard, "p_mochi"

def p_sour_runtz_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["p_sour_runtz"](qty)
    text = ("🇺🇸⭐<b>Sour RUNTZ</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🇺🇸⭐CALI • Stock 92.50 • ★ 4.9 (18)\n\n"
            "AAAA cali imports, simply perfect smoke.\n\n"
            "Sour Runtz\n\n"
            "A balanced hybrid with a sweet, candy-like flavor backed by a tangy sour kick. "
            "Offers a euphoric, uplifting high that slowly transitions into a relaxed body buzz—great "
            "for chilling without full couch-lock. Potent, so beginners should pace themselves.\n\n"
            "from <b>3.50</b> gr @ £10.00\n"
            "from <b>7.00</b> gr @ £8.88\n"
            "from <b>14.00</b> gr @ £7.77\n"
            "from <b>28.00</b> gr @ £7.20\n"
            "from <b>56.00</b> gr @ £6.50\n"
            "from <b>125.00</b> gr @ £6.00")
    keyboard = build_mcw_keyboard("p_sour_runtz", "cat_cali", 3.5, qty, price, chat_id, reviews=18)
    return text, keyboard, "p_sour_runtz"

def p_fire_runtz_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["p_fire_runtz"](qty)
    text = ("🇺🇸⭐<b>Fire Runtz</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🇺🇸⭐CALI • Stock 180.00 • ★ 5.0 (6)\n\n"
            "AAAA cali imports, simply perfect smoke.\n\n"
            "Fire Runtz is an even hybrid (about 50% indica / 50% sativa) known for its sweet, sugary, "
            "candy-like flavor. It delivers a balanced high—uplifting and euphoric at first with a smooth, "
            "relaxing finish—making it great for good vibes without too much sedation.\n\n"
            "from <b>3.50</b> gr @ £10.00\n"
            "from <b>7.00</b> gr @ £8.88\n"
            "from <b>14.00</b> gr @ £7.77\n"
            "from <b>28.00</b> gr @ £7.20\n"
            "from <b>56.00</b> gr @ £6.50\n"
            "from <b>125.00</b> gr @ £6.00")
    keyboard = build_mcw_keyboard("p_fire_runtz", "cat_cali", 3.5, qty, price, chat_id, reviews=6)
    return text, keyboard, "p_fire_runtz"

def p_dosi_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["p_dosi"](qty)
    text = ("🇺🇸⭐<b>Dosi Cakes</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🇺🇸⭐CALI • Stock 1,365.00 • ★ 5.0 (14)\n\n"
            "AAAA cali imports, simply perfect smoke.\n\n"
            "Dosi Cakes is an indica-dominant hybrid (around 70% indica / 30% sativa) with a sweet, "
            "dessert-like flavor and creamy aroma. It brings a deeply relaxing, calm-focused high that "
            "melts into the body and can be great for stress relief or evening chill—best enjoyed when "
            "you're winding down, not trying to stay productive.\n\n"
            "from <b>3.50</b> gr @ £10.00\n"
            "from <b>7.00</b> gr @ £8.88\n"
            "from <b>14.00</b> gr @ £7.77\n"
            "from <b>28.00</b> gr @ £7.20\n"
            "from <b>56.00</b> gr @ £6.50\n"
            "from <b>125.00</b> gr @ £6.00\n"
            "from <b>250.00</b> gr @ £5.80")
    keyboard = build_mcw_keyboard("p_dosi", "cat_cali", 3.5, qty, price, chat_id, reviews=14)
    return text, keyboard, "p_dosi"

def p_iceage_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["p_iceage"](qty)
    text = ("🇺🇸⭐<b>Ice Age</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🇺🇸⭐CALI • Stock 738.50 • ★ 5.0 (5)\n\n"
            "AAAA cali imports, simply perfect smoke.\n\n"
            "Ice Age is an indica-dominant hybrid (usually around 70% indica / 30% sativa) that's known "
            "for its frosty, resin-covered buds and cool, sharp aroma. The flavor mixes earthy and sweet "
            "pine with a hint of spice, while the effect leans deeply relaxing and sedating — great for "
            "kicking back in the evening or before bed, but not ideal if you need to stay active.\n\n"
            "from <b>3.50</b> gr @ £10.00\n"
            "from <b>7.00</b> gr @ £8.88\n"
            "from <b>14.00</b> gr @ £7.77\n"
            "from <b>28.00</b> gr @ £7.20\n"
            "from <b>56.00</b> gr @ £6.50\n"
            "from <b>125.00</b> gr @ £6.00\n"
            "from <b>250.00</b> gr @ £5.80")
    keyboard = build_mcw_keyboard("p_iceage", "cat_cali", 3.5, qty, price, chat_id, reviews=5)
    return text, keyboard, "p_iceage"

def p_twix_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["p_twix"](qty)
    text = ("🇺🇸⭐<b>Twix Gushers</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🇺🇸⭐CALI • Stock 44.00 • ★ 5.0 (7)\n\n"
            "AAAA cali imports, simply perfect smoke.\n\n"
            "Twix Gushers is an indica-dominant hybrid (about 60% indica / 40% sativa) with a sweet, "
            "candy-like flavor and creamy fruit notes. The high starts happy and uplifting, then melts "
            "into a smooth, relaxing body buzz—great for chilling and unwinding, not for getting stuff done.\n\n"
            "from <b>3.50</b> gr @ £10.00\n"
            "from <b>7.00</b> gr @ £8.88\n"
            "from <b>14.00</b> gr @ £7.77\n"
            "from <b>28.00</b> gr @ £7.20\n"
            "from <b>56.00</b> gr @ £6.50\n"
            "from <b>125.00</b> gr @ £6.00\n"
            "from <b>250.00</b> gr @ £5.80")
    keyboard = build_mcw_keyboard("p_twix", "cat_cali", 3.5, qty, price, chat_id, reviews=7)
    return text, keyboard, "p_twix"

def p_hash_bundle_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 1.0
    price = PRICE_FNS["p_hash_bundle"](qty)
    text = ("💎 <b>20g Hash Bundle – 80GBP</b> 💎\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🔥BUNDLES • Stock 8.00 • ★ 5.0 (17)\n\n"
            "💎 20g Hash Bundle – 80GBP 💎\n\n"
            "⚠️ Bundle weight and strains are fixed — no changes or swaps.\n\n"
            "🔥 Includes 4 labels (5g each):\n"
            "•Che Guevara\n• Drago\n• KETAMA\n• Donkey Kong\n\n"
            "Perfect mix. Smooth smoke. Heavy hits.\nSolid value for quality hash.\n\n"
            "from <b>1.00</b> pcs @ £80.00")
    keyboard = build_mcw_keyboard("p_hash_bundle", "cat_bundles", 1.0, qty, price, chat_id, reviews=17, unit="pcs")
    return text, keyboard, None

def p_mixmatch_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 1.0
    price = PRICE_FNS["p_mixmatch"](qty)
    text = ("🔥 <b>Mix &amp; Match CALI bundle</b> 🔥\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🔥BUNDLES • Stock Unlimited • ★ 4.9 (38)\n\n"
            "🔥 Mix &amp; Match CALI Deal 🔥\n\n"
            "🔥Unmatched quality\n\n"
            "Any 3 strains — 5g each, 15 grams total for 115GBP\n\n"
            "Available strains:\n\n"
            "• Twix Gushers\n• Ice Age\n• Dosi Cake\n• Fire Runtz\n• Sour Runtz\n\n"
            "from <b>1.00</b> pcs @ £115.00")
    keyboard = build_mcw_keyboard("p_mixmatch", "cat_bundles", 1.0, qty, price, chat_id, reviews=38, unit="pcs")
    return text, keyboard, "p_mixmatch"

def p_3uk_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 1.0
    price = PRICE_FNS["p_3uk"](qty)
    text = ("🚀 <b>3UK strains for £115 – 21g Deal</b> 🚀\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🔥BUNDLES • Stock Unlimited • ★ 5.0 (70)\n\n"
            "🚀 3UK strains, 7 g each for £115 – 21g Deal 🚀\n\n"
            "Pick any 3:\n\n"
            "🍦 Cream 47\n🍦 Gelato\n🍒 Cherry Lemonade\n🍒 Runtz\n"
            "⭐ Stardawg\n⭐ Amnesia Haze\n⭐ Cherpaya\n⭐ Godfather OG\n"
            "⭐ MIMOSA\n⭐ McCAKE\n⭐ CRIMINAL +\n⭐ Skywalker OG\n\n"
            "Top UK flavours. Mix &amp; match.\n\n"
            "from <b>1.00</b> pcs @ £115.00")
    keyboard = build_mcw_keyboard("p_3uk", "cat_bundles", 1.0, qty, price, chat_id, reviews=70, unit="pcs")
    return text, keyboard, "p_3uk"

def p_survbox_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 1.0
    price = PRICE_FNS["p_survbox"](qty)
    text = ("🔥🔥 <b>BUDGET SURVIVAL BOX</b> 🔥🔥\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🔥BUNDLES • Stock 6.00 • ★ 5.0 (112)\n\n"
            "📦 50 GRAMS FOR £99\n"
            "⚠️ Bundle weight and strains are fixed — no changes or swaps.\n\n"
            "🌿 Inside the box:\n\n"
            "20g Shake\n\n"
            "20g SMALLS  stardawg\n\n"
            "10g Hash\n    👉 5g Donkey Kong 5g DRAGO\n\n"
            "⚖️ TOTAL: 50g\n"
            "💸 Built for bulk lovers &amp; end-of-month missions\n\n"
            "✅ Pure value\n⏳ LIMITED STOCK\n\n"
            "from <b>1.00</b> pcs @ £99.00")
    keyboard = build_mcw_keyboard("p_survbox", "cat_bundles", 1.0, qty, price, chat_id, reviews=112, unit="pcs")
    return text, keyboard, "p_survbox"

def p_vape1g_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 1.0
    price = PRICE_FNS["p_vape1g"](qty)
    text = ("☁️<b>1g Disposable vapes</b>☁️\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → ☁️VAPES☁️ • Stock 840.00 • ★ 5.0 (24)\n\n"
            "MELTx PACKMAN\n\n"
            "10 strains:\nLCG\nPeaches and Cream\nBruce Banner\nTropical Burst\nSpace Cookies\n"
            "Strawberry Gelato\nBuba Kush\nBlack Mamba\nLychee Cream\nTriple Burger\n\n"
            "Cali imported and filled by McWhites in UK with premium distillate and terps.\n\n"
            "from <b>1.00</b> pcs @ £22.00\n"
            "from <b>2.00</b> pcs @ £20.00\n"
            "from <b>5.00</b> pcs @ £19.00\n"
            "from <b>10.00</b> pcs @ £18.00\n"
            "from <b>20.00</b> pcs @ £17.00\n"
            "from <b>30.00</b> pcs @ £16.00\n"
            "from <b>50.00</b> pcs @ £15.00")
    keyboard = build_mcw_keyboard("p_vape1g", "cat_vapes", 1.0, qty, price, chat_id, reviews=24, unit="pcs")
    return text, keyboard, None

_STARDAWG_OFFER_TIERS = [(10,4.90),(20,4.70),(30,4.50),(50,4.30),(100,4.00),(200,3.80)]
_STARDAWG_SM_TIERS    = [(20,3.95),(30,3.70),(50,3.50),(100,3.25),(200,3.00)]

# --- UK grown flowers ---

_UK_FLOWER_TIERS = [(3.5,8.00),(7,7.00),(14,6.10),(28,5.40),(56,4.90),(112,4.65)]
_UK_FLOWER_PRICE_LINE = ("from <b>3.50</b> gr @ £8.00\n"
                          "from <b>7.00</b> gr @ £7.00\n"
                          "from <b>14.00</b> gr @ £6.10\n"
                          "from <b>28.00</b> gr @ £5.40\n"
                          "from <b>56.00</b> gr @ £4.90\n"
                          "from <b>112.00</b> gr @ £4.65")

def flower_skywalker_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["flower_skywalker"](qty)
    text = ("🇬🇧<b>Skywalker OG</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻 • Stock 480.50 • ★ 5.0 (1)\n\n"
            "Skywalker OG is a classic heavy hitter. Earthy, piney flavor with a hint of spice and citrus. "
            "Hits with a calming body buzz and deep relaxation that melts away stress. Perfect for winding "
            "down at night and getting some solid sleep.\n\n" + _UK_FLOWER_PRICE_LINE)
    keyboard = build_mcw_keyboard("flower_skywalker", "cat_uk_flowers", 3.5, qty, price, chat_id, reviews=1)
    return text, keyboard, "flower_skywalker"

def flower_runtz_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["flower_runtz"](qty)
    text = ("🇬🇧<b>RUNTZ</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻 • Stock 50.00 • ★ 5.0 (1)\n\n"
            "Runtz is an evenly balanced hybrid strain (50% indica/50% sativa) created through a delicious "
            "cross of the infamous Zkittlez X Gelato strains. Named for the iconic candy, Runtz brings on "
            "a super delicious fruity flavor with tropical citrus and sour berries galore.\n\n" + _UK_FLOWER_PRICE_LINE)
    keyboard = build_mcw_keyboard("flower_runtz", "cat_uk_flowers", 3.5, qty, price, chat_id, reviews=1)
    return text, keyboard, "flower_runtz"

def flower_criminal_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["flower_criminal"](qty)
    text = ("🇬🇧⭐<b>CRIMINAL+</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻 • Stock 1,085.50 • ★ 4.5 (2)\n\n"
            "Criminal+ is a hard-hitting indica-style hybrid with a dark, gassy profile layered with earthy "
            "pine, pepper, and a slightly sweet herbal finish. The high is usually heavy, calming, and deeply "
            "physical — the kind of strain that slows everything down after a few pulls.\n\n" + _UK_FLOWER_PRICE_LINE)
    keyboard = build_mcw_keyboard("flower_criminal", "cat_uk_flowers", 3.5, qty, price, chat_id, reviews=2)
    return text, keyboard, "flower_criminal"

def flower_mccake_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["flower_mccake"](qty)
    text = ("🇬🇧⭐<b>McCAKE</b>🇬🇧\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻 • Stock 1,891.50 • ★ 5.0 (3)\n\n"
            "McCake is a rich, heavy hybrid known for sweet vanilla cake notes mixed with diesel, pepper, "
            "and earthy gas. It usually leans relaxing and euphoric, with a strong body effect that creeps "
            "up after the first few hits.\n\n" + _UK_FLOWER_PRICE_LINE)
    keyboard = build_mcw_keyboard("flower_mccake", "cat_uk_flowers", 3.5, qty, price, chat_id, reviews=3)
    return text, keyboard, "flower_mccake"

def flower_stardawg_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["flower_stardawg"](qty)
    text = ("🇬🇧<b>STARDAWG</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻 • Stock 727.00 • ★ 5.0 (4)\n\n"
            "Stardawg\n\n"
            "Sativa-leaning hybrid (Chemdawg #4 × Tres Dawg) with a strong diesel, pine, and earthy flavor. "
            "Delivers a potent, euphoric head high that's energetic, talkative, and creative—more cerebral than heavy.\n\n"
            "Good for stress, mood, daytime use.\n\n" + _UK_FLOWER_PRICE_LINE)
    keyboard = build_mcw_keyboard("flower_stardawg", "cat_uk_flowers", 3.5, qty, price, chat_id, reviews=4)
    return text, keyboard, "flower_stardawg"

def flower_cherpaya_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["flower_cherpaya"](qty)
    text = ("🇬🇧<b>CHERPAYA</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻 • Stock 325.50 • ★ 5.0 (6)\n\n"
            "Cherpaya\n\n"
            "Indica-leaning hybrid (Papaya cross) with a sweet tropical mango/papaya + citrus flavor. "
            "Delivers a giddy, euphoric head high with some creativity, then settles into a relaxed, "
            "slightly heavy body buzz (can get couchy).\n\n"
            "Good for stress, mood boost, appetite.\n\n" + _UK_FLOWER_PRICE_LINE)
    keyboard = build_mcw_keyboard("flower_cherpaya", "cat_uk_flowers", 3.5, qty, price, chat_id, reviews=6)
    return text, keyboard, "flower_cherpaya"

def flower_amnesia_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["flower_amnesia"](qty)
    text = ("🇬🇧<b>Amnesia Haze</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻 • Stock 813.00 • ★ 5.0 (3)\n\n"
            "Classic Amnesia Haze\n\n"
            "Sativa-dominant strain with a zesty lemon/citrus + earthy flavor. Gives a strong, energetic "
            "and euphoric head high—very uplifting, clear-headed, and great for daytime.\n\n"
            "Good for mood boost, focus, social vibes.\n\n" + _UK_FLOWER_PRICE_LINE)
    keyboard = build_mcw_keyboard("flower_amnesia", "cat_uk_flowers", 3.5, qty, price, chat_id, reviews=3)
    return text, keyboard, "flower_amnesia"

def flower_cherry_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["flower_cherry"](qty)
    text = ("🇬🇧🍒<b>CHERRY LEMONADE</b>🍒🇬🇧\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻 • Stock 1,096.50 • ★ 5.0 (10)\n\n"
            "Cherry Lemonade\n\n"
            "A sativa-leaning hybrid with bright cherry and citrus flavors. Gives an uplifting, happy high "
            "with a light body buzz—great for daytime vibes and mood boost. Smooth but can feel a bit racy "
            "if overdone.\n\n" + _UK_FLOWER_PRICE_LINE)
    keyboard = build_mcw_keyboard("flower_cherry", "cat_uk_flowers", 3.5, qty, price, chat_id, reviews=10)
    return text, keyboard, "flower_cherry"

def flower_gelato_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["flower_gelato"](qty)
    text = ("🇬🇧🆕<b>Gelato</b>🇬🇧\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻 • Stock 100.00 • ★ 5.0 (5)\n\n"
            "Gelato: A flavorful hybrid with sweet, creamy dessert notes that delivers a balanced "
            "high—uplifting, euphoric, and relaxing without being overly sedating.\n\n" + _UK_FLOWER_PRICE_LINE)
    keyboard = build_mcw_keyboard("flower_gelato", "cat_uk_flowers", 3.5, qty, price, chat_id, reviews=5)
    return text, keyboard, "flower_gelato"

def flower_cream47_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["flower_cream47"](qty)
    text = ("🇬🇧🍦<b>Cream 47</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻 • Stock 739.50 • ★ 4.9 (11)\n\n"
            "Cream 47 is a balanced hybrid known for its sweet, creamy vanilla aroma layered with earthy "
            "and slightly sugary notes. The buds are resinous, offering a smooth and flavorful smoke.\n\n"
            "The high starts with a gentle cerebral lift that boosts mood and focus, followed by a light, "
            "relaxing body effect that doesn't feel too heavy. It's a versatile strain that works well for "
            "social settings, creative tasks, or winding down without full sedation.\n\n" + _UK_FLOWER_PRICE_LINE)
    keyboard = build_mcw_keyboard("flower_cream47", "cat_uk_flowers", 3.5, qty, price, chat_id, reviews=11)
    return text, keyboard, "flower_cream47"

def flower_godfather_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["flower_godfather"](qty)
    text = ("🇬🇧<b>Godfather OG</b>🇬🇧\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻 • Stock 212.50 • ★ 4.9 (24)\n\n"
            "Godfather OG is an indica dominant hybrid with high THC content and sedative effects. "
            "Read reviews from users who rated it as the best strain for insomnia, anxiety, depression "
            "and other conditions.\n\n" + _UK_FLOWER_PRICE_LINE)
    keyboard = build_mcw_keyboard("flower_godfather", "uk_flowers_p2", 3.5, qty, price, chat_id, reviews=24)
    return text, keyboard, "flower_godfather"

def flower_frosted_lemon_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["flower_frosted_lemon"](qty)
    text = ("🇬🇧<b>Frosted Lemon</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻 • Stock 986.00 • ★ 0.0 (0)\n\n"
            "Frosted Lemon is a bright, sativa dominant flavorful strain. Sweet lemon citrus up front "
            "with a smooth, creamy finish. Delivers an uplifting head buzz that keeps you feeling positive "
            "and focused while still providing a mellow sense of relaxation. Great daytime smoke with "
            "refreshing flavor.\n\n" + _UK_FLOWER_PRICE_LINE)
    keyboard = build_mcw_keyboard("flower_frosted_lemon", "uk_flowers_p2", 3.5, qty, price, chat_id, reviews=0)
    return text, keyboard, "flower_frosted_lemon"

def flower_wedding_cake_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 3.5
    price = PRICE_FNS["flower_wedding_cake"](qty)
    text = ("🇬🇧<b>Wedding Cake</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🌻UK grown flowers🌻 • Stock 993.00 • ★ 0.0 (0)\n\n"
            "Wedding Cake is a sweet, flavorful strain. Rich vanilla and sugary dough notes with a touch "
            "of earthiness. Delivers a relaxing body high paired with a happy, uplifting mood that keeps "
            "you feeling good without being too heavy. Great for kicking back and enjoying the moment.\n\n"
            + _UK_FLOWER_PRICE_LINE)
    keyboard = build_mcw_keyboard("flower_wedding_cake", "uk_flowers_p2", 3.5, qty, price, chat_id, reviews=0)
    return text, keyboard, "flower_wedding_cake"

# --- Hash products ---

def p_temple_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 5.0
    price = PRICE_FNS["p_temple"](qty)
    text = ("💣<b>TEMPLE BALLS</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🍫Hash🍫 • Stock 1,214.00 • ★ 4.9 (24)\n\n"
            "Temple Balls are old-school hash known for deep earthy, spicy, creamy smoke and a "
            "slow-building full-body high. The vibe is usually calm, euphoric, and heavy without the "
            "sharp \"head rush\" of modern concentrates.\n\n"
            "for 3 oz+ orders balls will be sent in original packaging.\n\n"
            "from <b>5.00</b> gr @ £7.00\n"
            "from <b>10.00</b> gr @ £6.50\n"
            "from <b>15.00</b> gr @ £6.25\n"
            "from <b>20.00</b> gr @ £6.00\n"
            "from <b>30.00</b> gr @ £5.75\n"
            "from <b>50.00</b> gr @ £5.50\n"
            "from <b>100.00</b> gr @ £5.00\n"
            "from <b>300.00</b> gr @ £4.20")
    keyboard = build_mcw_keyboard("p_temple", "cat_hash", 5.0, qty, price, chat_id, reviews=24)
    return text, keyboard, "p_temple"

def p_ketama_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 5.0
    price = PRICE_FNS["p_ketama"](qty)
    text = ("🇬🇧<b>KETAMA</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🍫Hash🍫 • Stock 273.00 • ★ 5.0 (10)\n\n"
            "Ketama Hash: A traditional Moroccan hash with earthy, spicy flavors that delivers a smooth, "
            "relaxing body high and mellow mental calm. 🌿💨\n\n"
            "from <b>5.00</b> gr @ £6.00\n"
            "from <b>10.00</b> gr @ £5.50\n"
            "from <b>15.00</b> gr @ £5.00\n"
            "from <b>20.00</b> gr @ £4.50\n"
            "from <b>30.00</b> gr @ £4.20\n"
            "from <b>50.00</b> gr @ £4.00\n"
            "from <b>100.00</b> gr @ £3.70")
    keyboard = build_mcw_keyboard("p_ketama", "cat_hash", 5.0, qty, price, chat_id, reviews=10)
    return text, keyboard, "p_ketama"

_LA_MOUSSE_DESC = ("La Mousse hash posted from UK. 4 labels available:\n\n"
                   "DONKEY KONG\nDRAGO\nFAMILY GUY\nCHE GUEVARA\n\n"
                   "from <b>5.00</b> gr @ £5.50\n"
                   "from <b>10.00</b> gr @ £5.20\n"
                   "from <b>20.00</b> gr @ £4.90\n"
                   "from <b>30.00</b> gr @ £4.50\n"
                   "from <b>40.00</b> gr @ £4.25\n"
                   "from <b>50.00</b> gr @ £4.00\n"
                   "from <b>100.00</b> gr @ £3.76\n"
                   "from <b>200.00</b> gr @ £3.50\n"
                   "from <b>300.00</b> gr @ £3.25\n"
                   "from <b>500.00</b> gr @ £3.10")

def p_che_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 5.0
    price = PRICE_FNS["p_che"](qty)
    text = ("✨🇬🇧<b>CHE GUEVARA</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🍫Hash🍫 • Stock 473.00 • ★ 4.9 (7)\n\n" + _LA_MOUSSE_DESC)
    keyboard = build_mcw_keyboard("p_che", "cat_hash", 5.0, qty, price, chat_id, reviews=7)
    return text, keyboard, "p_che"

def p_drago_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 5.0
    price = PRICE_FNS["p_drago"](qty)
    text = ("✨🇬🇧<b>DRAGO</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🍫Hash🍫 • Stock 135.00 • ★ 5.0 (2)\n\n" + _LA_MOUSSE_DESC)
    keyboard = build_mcw_keyboard("p_drago", "cat_hash", 5.0, qty, price, chat_id, reviews=2)
    return text, keyboard, "p_drago"

def p_donkey_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 5.0
    price = PRICE_FNS["p_donkey"](qty)
    text = ("✨🇬🇧<b>DONKEY KONG</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🍫Hash🍫 • Stock 450.00 • ★ 5.0 (5)\n\n" + _LA_MOUSSE_DESC)
    keyboard = build_mcw_keyboard("p_donkey", "cat_hash", 5.0, qty, price, chat_id, reviews=5)
    return text, keyboard, "p_donkey"

# --- Budget smoke products ---

def p_bss40_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 1.0
    price = PRICE_FNS["p_bss40"](qty)
    text = ("🍀<b>BLUEBERRY SUGAR SMALLS 40G</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🎒Budget smoke🎒 • Stock 0.00 • ★ 0.0 (0)\n\n"
            "🍀 OUR BLUEBERRY SUGAR popcorn bud 40G\n\n"
            "from <b>1.00</b> pcs @ £140.00\n"
            "from <b>2.00</b> pcs @ £135.00")
    keyboard = build_mcw_keyboard("p_bss40", "cat_budget", 1.0, qty, price, chat_id, reviews=0, unit="pcs")
    return text, keyboard, "p_bss40"

def p_mim25_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 1.0
    price = PRICE_FNS["p_mim25"](qty)
    text = ("🍀<b>MIMOSA SMALLS 25G</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🎒Budget smoke🎒 • Stock 0.00 • ★ 0.0 (0)\n\n"
            "🍀 OUR MIMOSA popcorn bud 25f\n\n"
            "from <b>1.00</b> pcs @ £89.00")
    keyboard = build_mcw_keyboard("p_mim25", "cat_budget", 1.0, qty, price, chat_id, reviews=0, unit="pcs")
    return text, keyboard, "p_mim25"

def p_stardawg_sm_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 20.0
    price = PRICE_FNS["p_stardawg_sm"](qty)
    text = ("🍀<b>STARDAWG SMALLS</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🎒Budget smoke🎒 • Stock 20.00 • ★ 5.0 (8)\n\n"
            "🍀 BUDGET SMOKE\n\n"
            "Good smoke for the price Stardawg Smalls, 25% THC there, heavy hitter\n\n"
            "from <b>20.00</b> gr @ £3.95\n"
            "from <b>30.00</b> gr @ £3.70\n"
            "from <b>50.00</b> gr @ £3.50\n"
            "from <b>100.00</b> gr @ £3.25\n"
            "from <b>200.00</b> gr @ £3.00")
    keyboard = build_mcw_keyboard("p_stardawg_sm", "cat_budget", 20.0, qty, price, chat_id, reviews=8)
    return text, keyboard, "p_stardawg_sm"

def p_shake_content(chat_id=None):
    qty = get_quantity(chat_id) if chat_id else 30.0
    price = PRICE_FNS["p_shake"](qty)
    text = ("🇬🇧🍃<b>SHAKE</b>\n"
            "🇬🇧UNITED KINGDOM🇬🇧 → 🎒Budget smoke🎒 • Stock 1,576.00 • ★ 5.0 (44)\n\n"
            "⚠️ Best value\n\n"
            "🇬🇧🍃SHAKE🍃🇬🇧\n"
            "shake, trim, dust, some small nugs\nPosted from UK\n\n"
            "⚠️ Best value\n\n"
            "from <b>30.00</b> gr @ £2.00\n"
            "from <b>100.00</b> gr @ £1.50\n"
            "from <b>200.00</b> gr @ £1.25")
    keyboard = build_mcw_keyboard("p_shake", "cat_budget", 30.0, qty, price, chat_id, reviews=44)
    return text, keyboard, "p_shake"

# =========================
# PRODUCT REGISTRY
# Structure: {prefix: (content_function, default_qty, step_value, product_name)}
# =========================

PRODUCT_UNITS = {
    "p_bar_hash":    "pcs",
    "p_hash_bundle": "pcs",
    "p_mixmatch":    "pcs",
    "p_3uk":         "pcs",
    "p_survbox":     "pcs",
    "p_vape1g":      "pcs",
    "p_bss40":       "pcs",
    "p_mim25":       "pcs",
}

PRODUCTS = {
    "kettle_10":     (kettle_10_content,     3.5,  0.5,  "Ketchup"),
    "kettle_indian": (indian_kettle_content,  3.5,  0.5,  "Indian Ketchup"),
    "kettle_dutch":  (dutch_kettle_content,  14.0,  3.5,  "Dutch Ketchup"),
    "eggwhite":      (eggwhite_content,       3.5,  0.5,  "Mdaffara"),
    "egg_black":     (egg_black_content,     10.0,  1.0,  "egg"),
    "brownpeanut_g": (brownpeanut_g_content, 25.0,  5.0,  "2psylliumg 25"),
    "brownpeanut_p": (brownpeanut_p_content, 10.0,  1.0,  "2psyllium 22"),
    "dpeanut":       (dpeanut_content,        1.0,  0.25, "dpsyllium"),
    "mpeanut_c":     (mpeanut_c_content,      2.0,  0.5,  "M psylium5G"),
    "apeanut_s":     (apeanut_s_content,     14.0,  3.5,  "AM psylium"),
    "speed_menu":    (speed_content,          7.0,  3.5,  "AStilton"),
    "cok":           (coke_content,           1.75, 0.25, "CStilton"),
    "mk":            (mkat_content,           3.5,  0.5,  "MStilton"),
    "p_offer_stardawg":  (p_offer_stardawg_content,  10.0, 10.0, "STARDAWG"),
    "p_offer_gelato41":  (p_offer_gelato41_content,  10.0, 10.0, "GELATO41"),
    "p_offer_bluenerdz": (p_offer_bluenerdz_content, 10.0, 10.0, "BLUE NERDZ"),
    "p_bar_hash":        (p_bar_hash_content,         1.0,  1.0, "1 BAR HASH 100G"),
    "p_icecream":        (p_icecream_content,         3.5,  3.5, "ICE CREAM CAKE"),
    "p_havana":          (p_havana_content,           1.0,  1.0, "Havana Nights"),
    "p_gelato_ext":      (p_gelato_ext_content,       1.0,  1.0, "Gelato Crumble"),
    "p_airheadz":        (p_airheadz_content,         1.0,  1.0, "AirHeadz"),
    "p_mochi":           (p_mochi_content,            1.0,  1.0, "Mochi Gelato"),
    "p_sour_runtz":      (p_sour_runtz_content,       3.5,  3.5, "Sour RUNTZ"),
    "p_fire_runtz":      (p_fire_runtz_content,       3.5,  3.5, "Fire Runtz"),
    "p_dosi":            (p_dosi_content,             3.5,  3.5, "Dosi Cakes"),
    "p_iceage":          (p_iceage_content,           3.5,  3.5, "Ice Age"),
    "p_twix":            (p_twix_content,             3.5,  3.5, "Twix Gushers"),
    "p_hash_bundle":     (p_hash_bundle_content,      1.0,  1.0, "20g Hash Bundle"),
    "p_mixmatch":        (p_mixmatch_content,         1.0,  1.0, "Mix & Match CALI bundle"),
    "p_3uk":             (p_3uk_content,              1.0,  1.0, "3UK strains 21g Deal"),
    "p_survbox":         (p_survbox_content,          1.0,  1.0, "BUDGET SURVIVAL BOX"),
    "p_vape1g":          (p_vape1g_content,           1.0,  1.0, "1g Disposable vapes"),
    "flower_skywalker":  (flower_skywalker_content,   3.5,  3.5, "Skywalker OG"),
    "flower_runtz":      (flower_runtz_content,        3.5,  3.5, "RUNTZ"),
    "flower_criminal":   (flower_criminal_content,     3.5,  3.5, "CRIMINAL+"),
    "flower_mccake":     (flower_mccake_content,       3.5,  3.5, "McCAKE"),
    "flower_stardawg":   (flower_stardawg_content,     3.5,  3.5, "STARDAWG"),
    "flower_cherpaya":   (flower_cherpaya_content,     3.5,  3.5, "CHERPAYA"),
    "flower_amnesia":    (flower_amnesia_content,      3.5,  3.5, "Amnesia Haze"),
    "flower_cherry":     (flower_cherry_content,       3.5,  3.5, "CHERRY LEMONADE"),
    "flower_gelato":     (flower_gelato_content,       3.5,  3.5, "Gelato (flower)"),
    "flower_cream47":    (flower_cream47_content,      3.5,  3.5, "Cream 47"),
    "flower_godfather":      (flower_godfather_content,      3.5,  3.5, "Godfather OG"),
    "flower_frosted_lemon":  (flower_frosted_lemon_content,  3.5,  3.5, "Frosted Lemon"),
    "flower_wedding_cake":   (flower_wedding_cake_content,   3.5,  3.5, "Wedding Cake"),
    "p_temple":          (p_temple_content,            5.0,  5.0, "TEMPLE BALLS"),
    "p_ketama":          (p_ketama_content,            5.0,  5.0, "KETAMA"),
    "p_che":             (p_che_content,               5.0,  5.0, "CHE GUEVARA"),
    "p_drago":           (p_drago_content,             5.0,  5.0, "DRAGO"),
    "p_donkey":          (p_donkey_content,            5.0,  5.0, "DONKEY KONG"),
    "p_bss40":           (p_bss40_content,             1.0,  1.0, "BLUEBERRY SUGAR SMALLS 40G"),
    "p_mim25":           (p_mim25_content,             1.0,  1.0, "MIMOSA SMALLS 25G"),
    "p_stardawg_sm":     (p_stardawg_sm_content,      10.0, 10.0, "STARDAWG SMALLS"),
    "p_shake":           (p_shake_content,            30.0, 30.0, "SHAKE"),
}

# =========================
# PRICE FUNCTIONS
# Maps product prefix to pricing function
# UPDATE these with your actual per-gram rates
# =========================

def _price(qty, rate): return round(qty * rate, 2)

PRICE_FNS = {
    "kettle_10":     lambda qty: _price(qty, 10.0),
    "kettle_indian": lambda qty: _price(qty, 10.0),
    "kettle_dutch":  lambda qty: _price(qty, 8.0),
    "eggwhite":      lambda qty: _price(qty, 10.0),
    "egg_black":     lambda qty: _price(qty, 8.0),
    "brownpeanut_g": lambda qty: _price(qty, 5.0),
    "brownpeanut_p": lambda qty: _price(qty, 5.0),
    "dpeanut":       lambda qty: _price(qty, 20.0),
    "mpeanut_c":     lambda qty: _price(qty, 15.0),
    "apeanut_s":     lambda qty: _price(qty, 10.0),
    "speed_menu":    lambda qty: _price(qty, 12.0),
    "cok":           lambda qty: _price(qty, 25.0),
    "mk":            lambda qty: _price(qty, 12.0),
    "p_offer_stardawg":  lambda qty: tier_price(qty, _STARDAWG_OFFER_TIERS),
    "p_offer_gelato41":  lambda qty: tier_price(qty, [(10,5.50),(20,5.20),(30,4.70),(50,4.40)]),
    "p_offer_bluenerdz": lambda qty: tier_price(qty, [(10,5.50),(20,5.20),(30,4.70),(50,4.40),(200,4.10)]),
    "p_bar_hash":        lambda qty: tier_price(qty, [(1,310.00),(2,295.00),(3,285.00),(5,270.00)]),
    "p_icecream":        lambda qty: tier_price(qty, [(3.5,9.50),(7,8.33),(14,7.40),(28,6.80),(56,6.20),(112,5.80),(250,5.40)]),
    "p_havana":          lambda qty: tier_price(qty, [(1,24.00),(2,22.00),(5,18.00),(10,17.00),(20,16.00),(50,14.50)]),
    "p_gelato_ext":      lambda qty: tier_price(qty, [(1,24.00),(2,22.00),(5,18.00),(10,17.00),(20,16.00),(50,14.50)]),
    "p_airheadz":        lambda qty: tier_price(qty, [(1,24.00),(2,22.00),(5,18.00),(10,17.00),(20,16.00),(50,14.50)]),
    "p_mochi":           lambda qty: tier_price(qty, [(1,24.00),(2,22.00),(5,18.00),(10,17.00),(20,16.00),(50,14.50)]),
    "p_sour_runtz":      lambda qty: tier_price(qty, [(3.5,10.00),(7,8.88),(14,7.77),(28,7.20),(56,6.50),(125,6.00)]),
    "p_fire_runtz":      lambda qty: tier_price(qty, [(3.5,10.00),(7,8.88),(14,7.77),(28,7.20),(56,6.50),(125,6.00)]),
    "p_dosi":            lambda qty: tier_price(qty, [(3.5,10.00),(7,8.88),(14,7.77),(28,7.20),(56,6.50),(125,6.00),(250,5.80)]),
    "p_iceage":          lambda qty: tier_price(qty, [(3.5,10.00),(7,8.88),(14,7.77),(28,7.20),(56,6.50),(125,6.00),(250,5.80)]),
    "p_twix":            lambda qty: tier_price(qty, [(3.5,10.00),(7,8.88),(14,7.77),(28,7.20),(56,6.50),(125,6.00),(250,5.80)]),
    "p_hash_bundle":     lambda qty: tier_price(qty, [(1,80.00)]),
    "p_mixmatch":        lambda qty: tier_price(qty, [(1,115.00)]),
    "p_3uk":             lambda qty: tier_price(qty, [(1,115.00)]),
    "p_survbox":         lambda qty: tier_price(qty, [(1,99.00)]),
    "p_vape1g":          lambda qty: tier_price(qty, [(1,22.00),(2,20.00),(5,19.00),(10,18.00),(20,17.00),(30,16.00),(50,15.00)]),
    "flower_skywalker":      lambda qty: tier_price(qty, _UK_FLOWER_TIERS),
    "flower_runtz":          lambda qty: tier_price(qty, _UK_FLOWER_TIERS),
    "flower_criminal":       lambda qty: tier_price(qty, _UK_FLOWER_TIERS),
    "flower_mccake":         lambda qty: tier_price(qty, _UK_FLOWER_TIERS),
    "flower_stardawg":       lambda qty: tier_price(qty, _UK_FLOWER_TIERS),
    "flower_cherpaya":       lambda qty: tier_price(qty, _UK_FLOWER_TIERS),
    "flower_amnesia":        lambda qty: tier_price(qty, _UK_FLOWER_TIERS),
    "flower_cherry":         lambda qty: tier_price(qty, _UK_FLOWER_TIERS),
    "flower_gelato":         lambda qty: tier_price(qty, _UK_FLOWER_TIERS),
    "flower_cream47":        lambda qty: tier_price(qty, _UK_FLOWER_TIERS),
    "flower_godfather":      lambda qty: tier_price(qty, _UK_FLOWER_TIERS),
    "flower_frosted_lemon":  lambda qty: tier_price(qty, _UK_FLOWER_TIERS),
    "flower_wedding_cake":   lambda qty: tier_price(qty, _UK_FLOWER_TIERS),
    "p_temple":          lambda qty: tier_price(qty, [(5,7.00),(10,6.50),(15,6.25),(20,6.00),(30,5.75),(50,5.50),(100,5.00),(300,4.20)]),
    "p_ketama":          lambda qty: tier_price(qty, [(5,6.00),(10,5.50),(15,5.00),(20,4.50),(30,4.20),(50,4.00),(100,3.70)]),
    "p_che":             lambda qty: tier_price(qty, [(5,5.50),(10,5.20),(20,4.90),(30,4.50),(40,4.25),(50,4.00),(100,3.76),(200,3.50),(300,3.25),(500,3.10)]),
    "p_drago":           lambda qty: tier_price(qty, [(5,5.50),(10,5.20),(20,4.90),(30,4.50),(40,4.25),(50,4.00),(100,3.76),(200,3.50),(300,3.25),(500,3.10)]),
    "p_donkey":          lambda qty: tier_price(qty, [(5,5.50),(10,5.20),(20,4.90),(30,4.50),(40,4.25),(50,4.00),(100,3.76),(200,3.50),(300,3.25),(500,3.10)]),
    "p_bss40":           lambda qty: tier_price(qty, [(1,140.00),(2,135.00)]),
    "p_mim25":           lambda qty: tier_price(qty, [(1,89.00)]),
    "p_stardawg_sm":     lambda qty: tier_price(qty, _STARDAWG_SM_TIERS),
    "p_shake":           lambda qty: tier_price(qty, [(30,2.00),(100,1.50),(200,1.25)]),
}

# =========================
# CATALOGUE
#
# The storefront tree: 6 categories, each split into sections, each holding
# products. Everything here is data — one entry per product — and the screen
# functions are built from it by _make_product_content below, so adding a
# product means adding a row, not writing another near-identical function.
#
# Stock and rating counts are as shown on the store at the time of writing.
# =========================

CALI_BLURB = "AAAA cali imports, simply perfect smoke."
THAI_BLURB = "🇹🇭THAI IMPORTED FLOWERS"

# Price tiers, shared by every product that sells on the same ladder.
_USA_TIERS      = [(3.5, 10.00), (7, 8.88), (14, 7.77), (28, 7.20), (56, 6.50), (125, 6.00)]
_USA_TIERS_250  = _USA_TIERS + [(250, 5.80)]
_IMPORT_TIERS   = [(3.5, 9.50), (7, 8.33), (14, 7.40), (28, 6.80),
                   (56, 6.20), (112, 5.80), (250, 5.40)]
_EXTRACT_TIERS  = [(1, 24.00), (2, 22.00), (5, 18.00), (10, 17.00), (20, 16.00), (50, 14.50)]
_HASH_FM_TIERS  = [(5, 12.00), (10, 10.00), (20, 9.00), (30, 8.00), (50, 7.50), (100, 7.00)]
_VAPE_TIERS     = [(1, 24.00), (2, 22.00)]
_EDIBLE_TIERS   = [(1, 15.00), (3, 14.00), (5, 13.00), (10, 12.00), (15, 11.00), (20, 10.00)]

HYBRID = "⚖️ HYBRID ⚖️"
SATIVA = "🔼 SATIVA 🔼"
INDICA = "🔽 INDICA 🔽"

# Cannabis → UK Grown / Hash, from the real product pages.
UK_GROWN_BLURB = ("ONLY PRE-SET WEIGHTS AVAILABLE\n\n"
                  "PLEASE NOTE: We do not offer mix and match, what you "
                  "order is what we will send.")
_UK_GROWN_PATH = f"Cannabis → UK Grown \U0001F1EC\U0001F1E7"
_UK_HASH_PATH = "Cannabis → Hash"

# Dissociatives → Ketamine / Ecstasy → MDMA.
_DISSOC_KETAMINE_PATH = "Dissociatives → Ketamine"
_ECSTASY_MDMA_PATH = "Ecstasy → MDMA"

# Shard Ketamine and Rock Ketamine sell on the identical ladder.
_KETAMINE_TIERS = [(3.5, 4.00), (7, 4.00), (14, 4.00), (28, 4.00),
                   (56, 3.75), (100, 3.25), (250, 2.50)]

# Stimulants → Methamphetamine. Tapping "Methamphetamine" in Stimulants opens
# a one-product sub-category, same shape as Ketamine/MDMA, just reached from
# a screen that also lists two real products directly - see
# stimulants_content below, which is hand-written because neither generic
# factory renders a mix of category links and product buttons on one screen.
_STIM_METH_PATH = "Stimulants → Methamphetamine"


def _tier_lines(tiers, unit):
    return "\n".join(f"from <b>{q:.2f}</b> {unit} @ £{r:.2f}" for q, r in tiers)


# prefix: (title, breadcrumb, stock, rating, reviews, blurb, description,
#          tiers, unit, back_callback)
CATALOGUE = {
    # ---- USA → HYBRID -----------------------------------------------------
    "usa_georgia_pie": (
        "🇺🇸⭐<b>Georgia Pie</b>", f"🇺🇸 USA 🇺🇸 → {HYBRID}", "662.00", "5.0", 5, CALI_BLURB,
        "A potent hybrid with sweet peach, creamy pastry, and earthy spice notes. It delivers "
        "a cheerful, euphoric buzz before settling into warm body relaxation.",
        _USA_TIERS, "gr", "usa_hybrid"),
    "usa_blueberry_bang": (
        "🇺🇸⭐<b>Blueberry Bang</b>", f"🇺🇸 USA 🇺🇸 → {HYBRID}", "390.50", "5.0", 1, CALI_BLURB,
        "Blueberry Bang is an indica-dominant hybrid known for its rich berry flavour and "
        "relaxing, well-balanced effects. It offers a delicious blend of sweet blueberries, "
        "ripe berries, earthy herbs, and subtle creamy notes with a smooth finish. Its effects "
        "begin with a happy, euphoric lift before easing into a soothing full-body relaxation.",
        _USA_TIERS, "gr", "usa_hybrid"),
    "usa_midnight_runtz": (
        "🇺🇸⭐<b>Midnight Runtz</b>", f"🇺🇸 USA 🇺🇸 → {HYBRID}", "432.50", "5.0", 2, CALI_BLURB,
        "Midnight Runtz is an indica-dominant hybrid. It features a rich flavour profile of "
        "sweet candy, ripe berries, tropical fruit, and creamy gas with a smooth earthy finish. "
        "Its effects are deeply relaxing yet euphoric, delivering an uplifting cerebral buzz "
        "that gradually settles into a calming full-body high.",
        _USA_TIERS, "gr", "usa_hybrid"),
    "usa_rockstar": (
        "🇺🇸⭐<b>ROCKSTAR</b>", f"🇺🇸 USA 🇺🇸 → {HYBRID}", "1,096.50", "0.0", 0, CALI_BLURB,
        "Rockstar is an indica-dominant hybrid known for its classic Kush profile, it delivers "
        "a rich blend of earthy pine, sweet grape, and spicy herbal flavours with subtle diesel "
        "undertones. Its effects are deeply relaxing and long-lasting, starting with a calming "
        "cerebral buzz before settling into a heavy full-body stone.",
        _USA_TIERS, "gr", "usa_hybrid"),
    "usa_jet_fuel_gelato": (
        "🇺🇸⭐<b>Get Fuel Gelato</b>", f"🇺🇸 USA 🇺🇸 → {HYBRID}", "408.00", "5.0", 1, CALI_BLURB,
        "Jet Fuel Gelato- Hybrid, often described as slightly indica-leaning. Sweet creamy vanilla, "
        "citrus and fruity Gelato flavours with strong diesel and earthy undertones. Effects are "
        "typically euphoric, uplifting and mentally stimulating at first, followed by a smooth, "
        "relaxing body buzz.",
        _USA_TIERS, "gr", "usa_hybrid"),
    "usa_cake_butter": (
        "🇺🇸⭐<b>Cake Butter</b>", f"🇺🇸 USA 🇺🇸 → {HYBRID}", "401.00", "5.0", 1, CALI_BLURB,
        "Cake Butter — Indica-leaning hybrid. Sweet vanilla cake, creamy butter and sugary dough "
        "flavours with subtle earthy, gassy notes. Effects are typically described as euphoric and "
        "uplifting initially, followed by smooth, calming body relaxation.",
        _USA_TIERS, "gr", "usa_hybrid"),
    "usa_omfg": (
        "🇺🇸⭐<b>OMFG</b>", f"🇺🇸 USA 🇺🇸 → {HYBRID}", "900.00", "0.0", 0, CALI_BLURB,
        "OMFG- Indica-dominant hybrid. Sweet grape candy, ripe berries and creamy fruit flavours "
        "with earthy, gassy undertones. Effects are typically euphoric, uplifting and relaxing, "
        "followed by a calming, body-heavy finish.",
        _USA_TIERS, "gr", "usa_hybrid"),
    "usa_red_runtz": (
        "🇺🇸⭐<b>Red Runtz</b>", f"🇺🇸 USA 🇺🇸 → {HYBRID}", "844.00", "0.0", 0, CALI_BLURB,
        "Red Runtz- Indica-leaning hybrid, commonly described as around 60% Indica / 40% Sativa. "
        "Sweet red berries, tropical candy and creamy fruit flavours with subtle earthy, gassy "
        "notes. Effects are typically euphoric, uplifting and happy at first, followed by a smooth, "
        "calming body relaxation.",
        _USA_TIERS, "gr", "usa_hybrid"),
    "usa_mac1": (
        "🇺🇸⭐<b>MAC 1</b>", f"🇺🇸 USA 🇺🇸 → {HYBRID}", "32.50", "5.0", 15, CALI_BLURB,
        "MAC 1 (Miracle Alien Cookies) is a balanced hybrid known for its stunning trichome "
        "coverage. Sweet, creamy and earthy flavours with hints of citrus and floral notes. "
        "Delivers a balanced high — uplifting and euphoric head buzz followed by a calming, "
        "relaxing body effect. Great for daytime or early evening use.",
        _USA_TIERS_250, "gr", "usa_hybrid"),

    # ---- USA → SATIVA -----------------------------------------------------
    "usa_mimosa": (
        "🇺🇸⭐<b>MIMOSA</b>", f"🇺🇸 USA 🇺🇸 → {SATIVA}", "988.00", "0.0", 0, CALI_BLURB,
        "Mimosa is a sativa-dominant hybrid, bursting with bright citrus flavour, it offers "
        "delicious notes of sweet orange, tropical fruit, and berries with a smooth, earthy "
        "finish. Its effects are uplifting, energetic, and mood-boosting, delivering a "
        "clear-headed cerebral buzz that encourages creativity and sociability.",
        _USA_TIERS, "gr", "usa_sativa"),
    "usa_pineapple_punch": (
        "🇺🇸⭐<b>Pineapple Punch</b>", f"🇺🇸 USA 🇺🇸 → {SATIVA}", "791.50", "5.0", 3, CALI_BLURB,
        "A sativa-dominant hybrid with sweet pineapple, tropical fruit, and citrus notes. It "
        "delivers an energetic, uplifting buzz thats great for daytime creativity and socializing.",
        _USA_TIERS, "gr", "usa_sativa"),
    "usa_wedding_crasher": (
        "🇺🇸⭐<b>WEDDING CRASHER</b>", f"🇺🇸 USA 🇺🇸 → {SATIVA}", "865.00", "0.0", 0, CALI_BLURB,
        "A sativa-leaning hybrid with sweet vanilla, grape, and earthy spice notes. It delivers "
        "an upbeat, social head buzz with gentle body relaxation.",
        _USA_TIERS_250, "gr", "usa_sativa"),
    "usa_cool_mint": (
        "🇺🇸⭐<b>Cool Mint</b>", f"🇺🇸 USA 🇺🇸 → {SATIVA}", "446.50", "5.0", 1, CALI_BLURB,
        "A sativa leaning hybrid with refreshing mint, sweet cookies, and earthy spice notes. "
        "It provides a mellow mental buzz followed by smooth, calming body relaxation.",
        _USA_TIERS_250, "gr", "usa_sativa"),
    "usa_banana_cooler": (
        "🇺🇸⭐<b>Banana Cooler</b>", f"🇺🇸 USA 🇺🇸 → {SATIVA}", "658.50", "5.0", 2, CALI_BLURB,
        "Sativa leaning hybrid with creamy banana, sweet dough, and light diesel notes. It "
        "delivers a happy, calming buzz with smooth full-body relaxation.",
        _USA_TIERS, "gr", "usa_sativa"),
    "usa_alpine_guava": (
        "🇺🇸⭐<b>Alpine Guava</b>", f"🇺🇸 USA 🇺🇸 → {SATIVA}", "686.50", "5.0", 3, CALI_BLURB,
        "Alpine Guava — Hybrid (Sativa-Leaning) brings a tropical burst of sweet guava, citrus, "
        "and fruity candy flavors with a light earthy finish. The high delivers an uplifting, "
        "happy head buzz followed by a mellow body relaxation that keeps you calm without "
        "slowing you down.",
        _USA_TIERS_250, "gr", "usa_sativa"),

    # ---- USA → INDICA -----------------------------------------------------
    "usa_wc_pink": (
        "🇺🇸⭐<b>WC PINK</b>", f"🇺🇸 USA 🇺🇸 → {INDICA}", "617.00", "5.0", 4, CALI_BLURB,
        "A hard-hitting indica with earthy pine, sweet kush, and strong fuel notes. It delivers "
        "deep body relaxation and a heavy, sleepy finish—best saved for nighttime.",
        _USA_TIERS_250, "gr", "usa_indica"),
    "usa_gummy_buns": (
        "🇺🇸⭐<b>Gummy Buns</b>", f"🇺🇸 USA 🇺🇸 → {INDICA}", "35.00", "5.0", 7, CALI_BLURB,
        "Gummy Buns — Indica-Dominant Hybrid is a dessert-style strain with sweet candy flavors, "
        "creamy dough, and hints of earthy gas on the finish. The high starts with a happy, "
        "euphoric lift before settling into a calming body buzz that melts away stress without "
        "being too overwhelming.",
        _USA_TIERS_250, "gr", "usa_indica"),
    "usa_death_bubba": (
        "🇺🇸⭐<b>Death Bubba</b>", f"🇺🇸 USA 🇺🇸 → {INDICA}", "497.50", "5.0", 12, CALI_BLURB,
        "Death Bubba is a potent indica known for its earthy pine flavor with sweet, skunky "
        "notes and a hint of spice. The high starts with a calm, euphoric head buzz before "
        "melting into a heavy, full-body relaxation that can leave you couch-locked and ready "
        "for sleep.",
        _USA_TIERS_250, "gr", "usa_indica"),
    "usa_blue_lobster": (
        "🇺🇸⭐<b>Blue Lobster</b>", f"🇺🇸 USA 🇺🇸 → {INDICA}", "425.50", "0.0", 0, CALI_BLURB,
        "Blue Lobster- Indica-dominant hybrid 60% Indica / 40% Sativa. Sweet blueberry, grape "
        "candy and creamy citrus flavours with subtle earthy, gassy undertones. Effects are "
        "typically euphoric, uplifting and happy at first, followed by smooth, calming body "
        "relaxation.",
        _USA_TIERS, "gr", "usa_indica"),
    "usa_animal_cookies": (
        "🇺🇸⭐<b>Animal Cookies</b>", f"🇺🇸 USA 🇺🇸 → {INDICA}", "767.00", "5.0", 2, CALI_BLURB,
        "Animal Cookies- Indica-dominant strain, roughly 75% Indica / 25% Sativa. Sweet vanilla "
        "cookies, earthy kush and nutty flavours with subtle spicy, gassy notes. Effects are "
        "typically euphoric, calming and deeply relaxing, with a strong, body-heavy finish.",
        _USA_TIERS, "gr", "usa_indica"),

    # ---- THAILAND ---------------------------------------------------------
    "thai_tropical_runtz": (
        "⭐🇹🇭<b>TROPICAL RUNTZ</b>", f"🇹🇭 THAILAND 🇹🇭 → {SATIVA}", "272.00", "5.0", 10, THAI_BLURB,
        "Tropical Runtz- sativa dominant hybrid bursts with sweet tropical fruit, citrus, and "
        "candy flavors backed by a subtle creamy gas. The uplifting, energetic high promotes "
        "happiness, creativity, and conversation before easing into a smooth, relaxing finish.",
        _IMPORT_TIERS, "gr", "thai_sativa"),
    "thai_sunset_sherbet": (
        "⭐🇹🇭<b>SUNSET SHERBET</b>", f"🇹🇭 THAILAND 🇹🇭 → {HYBRID}", "547.50", "5.0", 6, THAI_BLURB,
        "As indica dominant hybrid Sunset Sherbet delivers a rich blend of sweet berries, "
        "citrus, and creamy dessert flavors with subtle earthy notes. The high begins with an "
        "uplifting wave of euphoria before settling into a deeply relaxing body buzz, making it "
        "perfect for unwinding without feeling overly sedated.",
        _IMPORT_TIERS, "gr", "thai_hybrid"),

    # ---- EXTRACTS, VAPES & EDIBLES ---------------------------------------
    "ext_tangie": (
        "🇺🇸🍯<b>TANGIE</b>", "🧡Extracts, vapes & edibles → 🍯EXTRACTS", "181.00", "5.0", 4,
        "🇺🇸🍯TANGIE",
        "Tangie badder- Sativa-dominant hybrid, roughly 70% Sativa / 30% Indica. Bright tangerine, "
        "orange and citrus flavour with sweet, slightly skunky notes. Effects are typically uplifting, "
        "energetic and creative, with a clear-headed buzz.",
        _EXTRACT_TIERS, "gr", "evd_extracts"),
    "ext_gumbo": (
        "🇺🇸🍯<b>GUMBO</b>", "🧡Extracts, vapes & edibles → 🍯EXTRACTS", "173.00", "5.0", 5,
        "🇺🇸🍯GUMBO",
        "Gumbo badder- Indica-dominant hybrid, often around 65% Indica / 35% Sativa. Sweet bubblegum "
        "and fruity flavour with creamy, slightly earthy notes.",
        _EXTRACT_TIERS, "gr", "evd_extracts"),
    "ext_grape_gas": (
        "🇺🇸🍯<b>Grape Gas</b>", "🧡Extracts, vapes & edibles → 🍯EXTRACTS", "145.50", "5.0", 9,
        "🇺🇸🍯Grape Gas",
        "Grape Gas badder- Indica-dominant hybrid, commonly around 70% Indica / 30% Sativa. Sweet "
        "grape and berry flavour with strong diesel/gassy notes. Effects are typically calming and "
        "relaxing, with a heavy body finish.",
        _EXTRACT_TIERS, "gr", "evd_extracts"),
    "ext_runtz_scat_cookies": (
        "🇺🇸🍯<b>Runtz Scat Cookies</b>", "🧡Extracts, vapes & edibles → 🍯EXTRACTS", "179.00", "5.0", 1,
        "🇺🇸🍯Runtz Scat Cookies",
        "Runtz Scat Cookies badder- an indica-leaning hybrid. Sweet candy, creamy cookie and fruity "
        "flavours with gassy undertones. Effects are typically chilled and relaxing, with a pleasant "
        "body-heavy finish.",
        _EXTRACT_TIERS, "gr", "evd_extracts"),
    "ext_caramel_cream": (
        "🇺🇸🍯<b>Caramel Cream</b>", "🧡Extracts, vapes & edibles → 🍯EXTRACTS", "146.00", "5.0", 1,
        "🇺🇸🍯Caramel Cream",
        "Caramel Cream badder — usually an indica-dominant hybrid, around 70% Indica / 30% Sativa. "
        "Sweet caramel, vanilla and creamy flavour with earthy notes. Effects are typically calming, "
        "euphoric and deeply relaxing, with a warm body-heavy finish.",
        _EXTRACT_TIERS, "gr", "evd_extracts"),
    "ext_yahmi": (
        "🇺🇸🍯<b>Yahmi</b>", "🧡Extracts, vapes & edibles → 🍯EXTRACTS", "189.00", "5.0", 1,
        "🇺🇸🍯Yahmi",
        "Yahmi badder— typically an indica-dominant hybrid. Sweet, fruity and creamy flavour with "
        "gassy, earthy undertones. Effects are usually euphoric and relaxing, developing into a "
        "heavier, chilled body buzz.",
        _EXTRACT_TIERS, "gr", "evd_extracts"),
    "ext_rasberry_cough": (
        "🇺🇸🍯<b>Rasberry Cough</b>", "🧡Extracts, vapes & edibles → 🍯EXTRACTS", "173.00", "0.0", 0,
        "🇺🇸🍯Rasberry Cough",
        "Raspberry Cough badder— Sativa-dominant hybrid, roughly 70% Sativa / 30% Indica. Sweet "
        "raspberry and berry flavour with slightly earthy, spicy notes. Effects are typically "
        "uplifting, energetic and clear-headed, with a gentle relaxing finish.",
        _EXTRACT_TIERS, "gr", "evd_extracts"),
    "ext_tyson_glue": (
        "🇺🇸🍯<b>Tyson Glue</b>", "🧡Extracts, vapes & edibles → 🍯EXTRACTS", "180.00", "5.0", 4,
        "🇺🇸🍯Tyson Glue",
        "Tyson Glue badder— Indica-dominant hybrid, usually around 70% Indica / 30% Sativa. Pungent "
        "diesel, earthy and piney taste with slightly sweet notes. Effects are typically strong, "
        "euphoric and deeply relaxing, becoming heavy and calming.",
        _EXTRACT_TIERS, "gr", "evd_extracts"),
    "ext_lemon_cherry_gelato": (
        "🇺🇸🍯<b>Lemon Cherry Gelato</b>", "🧡Extracts, vapes & edibles → 🍯EXTRACTS", "139.00", "5.0", 3,
        "🇺🇸🍯Lemon Cherry Gelato",
        "Lemon Cherry Gelato badder (LCG) — Indica-leaning hybrid, roughly 60% Indica / 40% Sativa. "
        "Sweet cherry and zesty lemon flavour with creamy, slightly earthy notes.",
        _EXTRACT_TIERS, "gr", "evd_extracts"),
    "ext_cream_sherbet": (
        "🇺🇸🍯<b>CREAM SHERBET</b>", "🧡Extracts, vapes & edibles → 🍯EXTRACTS", "20.00", "5.0", 10,
        "🇺🇸🍯CREAM SHERBET",
        "Cream Sherbet mini review: A balanced hybrid with creamy vanilla, sweet berry, and "
        "light diesel notes. It delivers an upbeat, social buzz with smooth body relaxation.",
        _EXTRACT_TIERS, "gr", "evd_extracts"),
    "ext_bloody_mary": (
        "🇺🇸🍯<b>BLOODY MARY</b>", "🧡Extracts, vapes & edibles → 🍯EXTRACTS", "71.00", "5.0", 5,
        "🇺🇸🍯BLOODY MARY",
        "A bold hybrid with sweet fruit, earthy spice, and funky notes. It offers an uplifting "
        "head buzz followed by mellow body relaxation.",
        _EXTRACT_TIERS, "gr", "evd_extracts"),
    "ext_stoney_og": (
        "🇺🇸🍯<b>STONEY OG</b>", "🧡Extracts, vapes & edibles → 🍯EXTRACTS", "Unlimited", "5.0", 17,
        "🇺🇸STONEY OG",
        "An indica-dominant hybrid with earthy pine, citrus, and diesel notes. It brings a "
        "mellow cerebral buzz followed by heavy body relaxation—ideal for quiet evenings.",
        _EXTRACT_TIERS, "gr", "evd_extracts"),
    "edi_funnies": (
        "🤪<b>300MG FUNNIES</b>🤪", "🧡Extracts, vapes & edibles → 🤪EDIBLES", "430.00", "5.0", 4,
        "5 FRUIT FLAVOUR GUMMIES PER PACK , ~60MG per gummie", "",
        _EDIBLE_TIERS, "pcs", "evd_edibles"),

    # ---- HASH → PREMIUM BLEND 24K FULL MELT -----------------------------------
    "hash_godfather_db": (
        "<b>Godfather OG x Death Bubba</b>", "🍫HASH → ⭐Premium Blend 24k Full Melt",
        "456.00", "5.0", 1, "",
        "Premium Blend 24k Full Melt Full Spectrum Hash delivers an opulent experience that lives up "
        "to its regal name. This solventless concentrate is crafted from meticulously selected premium "
        "flower, resulting in a golden, sand-like texture that melts completely on contact with heat. "
        "Its full-spectrum profile captures the complete essence of the plant, offering a rich, "
        "layered experience that hash enthusiasts chase.",
        _HASH_FM_TIERS, "gr", "hash_full_melt"),
    "hash_guava_pbb": (
        "<b>Guava Kush + PBB</b>", "🍫HASH → ⭐Premium Blend 24k Full Melt",
        "485.00", "0.0", 0, "",
        "Premium Blend 24k Full Melt Full Spectrum Hash delivers an opulent experience that lives up "
        "to its regal name. This solventless concentrate is crafted from meticulously selected premium "
        "flower, resulting in a golden, sand-like texture that melts completely on contact with heat. "
        "Its full-spectrum profile captures the complete essence of the plant, offering a rich, "
        "layered experience that hash enthusiasts chase.",
        _HASH_FM_TIERS, "gr", "hash_full_melt"),
    "hash_slurricane": (
        "<b>Slurricane</b>", "🍫HASH → ⭐Premium Blend 24k Full Melt",
        "481.00", "5.0", 1, "",
        "Premium Blend 24k Full Melt Full Spectrum Hash delivers an opulent experience that lives up "
        "to its regal name. This solventless concentrate is crafted from meticulously selected premium "
        "flower, resulting in a golden, sand-like texture that melts completely on contact with heat. "
        "Its full-spectrum profile captures the complete essence of the plant, offering a rich, "
        "layered experience that hash enthusiasts chase.",
        _HASH_FM_TIERS, "gr", "hash_full_melt"),
    "hash_pink_kush": (
        "<b>Pink Kush</b>", "🍫HASH → ⭐Premium Blend 24k Full Melt",
        "455.00", "5.0", 2, "",
        "Premium Blend 24k Full Melt Full Spectrum Hash delivers an opulent experience that lives up "
        "to its regal name. This solventless concentrate is crafted from meticulously selected premium "
        "flower, resulting in a golden, sand-like texture that melts completely on contact with heat. "
        "Its full-spectrum profile captures the complete essence of the plant, offering a rich, "
        "layered experience that hash enthusiasts chase.",
        _HASH_FM_TIERS, "gr", "hash_full_melt"),

    # ---- HIGH for LESS — new products -------------------------------------
    "hfl_full_melt_bundle": (
        "🐝<b>4 Strains 20G FULL MELT bundle</b>🐝", "💷HIGH for LESS💷 → 🤝Bundles🤝",
        "20.00", "0.0", 0, "",
        "4 strains, 5g each, 20g total - FULL melt hash: 5g Slurricane, 5g Guava Kush + PBB, "
        "5g Pink Kush, 5g Godfather OG x Death Bubba",
        [(1, 150.00), (2, 140.00)], "pcs", "hfl_bundles"),
    "hfl_mazar": (
        "🕌<b>Mazar i Shariff</b>", "💷HIGH for LESS💷 → 🍫Hash",
        "1962.00", "5.0", 1, "",
        "Mazar i Shariff Afghanistan hash, aprox 15% THC. good hash for bucks",
        [(5, 6.00), (10, 5.50), (20, 5.00), (30, 4.75), (50, 4.50), (100, 4.25)], "gr", "hfl_hash"),
    "hfl_icc_smalls": (
        "🍀<b>Ice Cream Cake smalls</b>", "💷HIGH for LESS💷 → 🍿Popcorn buds",
        "2272.00", "5.0", 10, "🍀BUDGET SMOKE",
        "Good smoke for the price Small buds",
        [(20, 4.20), (30, 3.70), (50, 3.50), (100, 3.25)], "gr", "hfl_popcorn"),

    # ---- PUFFCO ------------------------------------------------------------
    "p_puffco_bundle": (
        "⭐<b>PUFFCO⭐ 10G EXTRACTS BUNDLE</b>", "⭐PUFFCO⭐10 G EXTRACTS BUNDLE",
        "Unlimited", "0.0", 0, "",
        "Something special for our community! ❤️\n"
        "We've put together a 10G EXTRACTS BUNDLE with 14 strains available — "
        "pick your own selection:\n"
        "💎 4 × 2.5G = 10G\n"
        "🔥 14 STRAINS TO CHOOSE FROM\n"
        "Every Extracts Bundle gives you a chance to win one of 3 great prizes:\n"
        "🥇 1st Prize — Puffco New Peak Pro\n"
        "🥈 2nd Prize — Puffco New Peak\n"
        "🥉 3rd Prize — Puffco New Proxy\n"
        "📅 WINNING ORDER NUMBERS WILL BE ANNOUNCED ON 2ND OCTOBER\n"
        "Good luck everyone! 🍀❤️",
        [(1, 150.00)], "pcs", "cat_puffco"),

    # ---- Cannabis → UK Grown -----------------------------------------------
    "uk_special_queen": (
        "<b>Special Queen \U0001F451✨</b>", _UK_GROWN_PATH, "256.50", "4.8", 31,
        UK_GROWN_BLURB,
        "Special Queen \U0001F451✨\n\n"
        "A premium Special Queen hybrid producing dense, resin-rich buds with "
        "bright citrus, subtle cheese funk, and classic haze notes balanced "
        "by a smooth earthy finish. Delivers an uplifting, happy head high "
        "followed by a calm, relaxing body effect that remains clear, "
        "balanced, and easy to enjoy.",
        [(3.5, 7.14), (7, 6.78), (14, 6.07), (28, 5.35), (63, 5.00),
         (125, 4.68), (250, 4.50)], "gr", "cat_cannabis_uk"),
    "uk_cereal_milk": (
        "<b>Cereal Milk \U0001F963\U0001F95B</b>", _UK_GROWN_PATH, "1413.00", "4.9", 56,
        UK_GROWN_BLURB,
        "Cereal Milk \U0001F963\U0001F95B\n\n"
        "A Snowman × Y-Life hybrid with chunky, frosty buds and a "
        "creamy, sugary cereal aroma. Gives a clean, uplifting buzz paired "
        "with mellow body calm.",
        [(3.5, 7.14), (7, 6.42), (14, 5.71), (28, 5.00), (63, 4.76),
         (125, 4.48), (250, 4.40)], "gr", "cat_cannabis_uk"),
    "uk_strawberry_cheesecake": (
        "<b>Strawberry Cheesecake \U0001F353\U0001F9C0</b>", _UK_GROWN_PATH,
        "166.50", "4.9", 447,
        UK_GROWN_BLURB,
        "Strawberry Cheesecake \U0001F353\U0001F9C0\n\n"
        "A UK-grown Cheese × Chronic × White Widow hybrid with "
        "dense, crystal-rich buds and a sweet strawberry–vanilla aroma. "
        "Provides gentle euphoria and soothing body relaxation.",
        [(3.5, 7.14), (7, 6.78), (14, 6.07), (28, 5.35), (63, 5.00),
         (125, 4.68), (250, 4.50)], "gr", "cat_cannabis_uk"),

    # ---- Cannabis → Hash ----------------------------------------------------
    # The leading emoji on this one is a best-effort read of a small, low-res
    # icon in the screenshot - check it against the real product page and
    # swap it if it's not the one actually used.
    "hash_cali_plates": (
        "<b>\U0001F9E8 Cali Plates Static Sift – Chocolato "
        "\U0001F1FA\U0001F1F8 70-130u</b>", _UK_HASH_PATH, "33.50", "4.8", 72,
        "CALI PLATES - Static Hash 70-130u",
        "\U0001F9E8 Cali Plates Static Sift – Chocolato \U0001F1FA\U0001F1F8\n\n"
        "Cali Plates import \U0001F1FA\U0001F1F8 with rich chocolate "
        "flavours and smooth, dessert-style terps. \U0001F9E8\U0001F525 "
        "Premium static sift for a clean, flavour-packed experience. "
        "\U0001F4A8\U0001F48E Quality you can taste. ✨",
        [(3.5, 10.00), (7, 8.58), (14, 7.50), (28, 6.79), (50, 6.40),
         (100, 6.00)], "gr", "cat_cannabis_hash"),

    # ---- Dissociatives → Ketamine -------------------------------------------
    "ds_shard_ketamine": (
        "<b>SHARD KETAMINE</b>", _DISSOC_KETAMINE_PATH, "1,394.00", "4.8", 18,
        "", "Experience a journey with SirMonopolys Shard Ketamine, whether "
        "you like to mix with beers at a rave or fancy yourself some power "
        "CK model lines at the after party, we have you covered\n\n"
        "Always top draw from Monopoly",
        _KETAMINE_TIERS, "gr", "cat_dissoc_ketamine"),
    "ds_rock_ketamine": (
        "<b>ROCK KETAMINE</b>", _DISSOC_KETAMINE_PATH, "1,156.00", "4.6", 23,
        "", "Experience a journey with SirMonopolys Rock Ketamine, whether "
        "you like to mix with beers at a rave or fancy yourself some power "
        "CK model lines at the after party, we have you covered\n\n"
        "Always top draw from Monopoly",
        _KETAMINE_TIERS, "gr", "cat_dissoc_ketamine"),

    # ---- Ecstasy → MDMA -------------------------------------------------------
    "ec_8ball_250": (
        "<b>8 Ball 250mg \U0001F1F3\U0001F1F1</b>", _ECSTASY_MDMA_PATH,
        "3,642.00", "5.0", 6,
        "", "Experience a journey with SirMonopolys 8 Ball 250mg, whether "
        "you like to mix with beers at a rave or fly the night away in "
        "heavenly bliss with a special someone we have you covered\n\n"
        "Always top draw from Monopoly",
        [(5, 3.75), (10, 2.75), (50, 1.75), (100, 1.50)], "pcs",
        "cat_ecstasy_mdma"),
    "ec_dutch_mdma": (
        "<b>Dutch MDMA \U0001F1F3\U0001F1F1</b>", _ECSTASY_MDMA_PATH,
        "252.50", "4.8", 12,
        "", "Experience a journey with SirMonopolys Dutch MDMA, whether you "
        "like to mix with beers at a rave or fly the night away in "
        "heavenly bliss with a special someone we have you covered\n\n"
        "Always top draw from Monopoly",
        [(3.5, 9.50), (7, 8.50), (14, 7.50), (28, 6.50), (56, 5.50)], "gr",
        "cat_ecstasy_mdma"),

    # ---- Opiates --------------------------------------------------------------
    # The description cuts off mid-sentence ("Tested and checked - cle") in
    # the screenshot itself, not from anything lost here - the screen may run
    # on further below the fold; send the rest if there is more.
    "op_afghan_black": (
        "<b>Afghani Black Glass \U0001F1E6\U0001F1EB</b>", "Opiates",
        "793.05", "4.9", 516,
        "", "AFGHANI BLACK GLASS HEROIN\n\n"
        "Stronger than the Iranian Black Glass\n\n"
        "A completely unique product - a first of its kind on tesseract. "
        "Our Black Glass Heroin sits atop the food chain, the great white "
        "shark of block gear.\n\n"
        "You know we only bring you the best products, so what are you "
        "waiting for... Order now!\n\n"
        "A superior grade \U0001F489  - untouched granting you a euphoric "
        "yet warm high with the legs of MoFarah, straight off the block "
        "and coming in rocks, what more could you ask for?\n\n"
        "Tested and checked - cle",
        [(0.25, 75.00), (0.5, 60.00), (1, 45.00), (2, 42.50), (3.5, 40.00),
         (7, 37.50), (14, 35.00), (28, 32.50)], "gr", "cat_opiates"),
    "op_pak4_china_white": (
        "<b>Pakistani #4 (China White)</b>", "Opiates", "924.20", "4.9", 294,
        "For SNIFF and IV ONLY\nNOT FOR SMOKING",
        "Ultra-refined #4 powder—silky off-white powder with "
        "pearl-like shimmer, melts seamlessly for an unrelenting, "
        "crystalline euphoria blast. Superior refinement (90%+) crafts "
        "waves of divine intensity minus the grit. Insiders obsession. "
        "Exclusive rush awaits! \U0001F31F",
        [(0.5, 80.00), (1, 65.00), (2, 62.50), (3.5, 60.00), (7, 55.00),
         (14, 50.00), (28, 42.50), (56, 40.00)], "gr", "cat_opiates"),

    # ---- Stimulants -------------------------------------------------------
    "st_pure_colombian": (
        "<b>PURE Columbian Cocaine \U0001F1E8\U0001F1F4</b>", "Stimulants",
        "2,309.00", "4.6", 53,
        "****Discount code works with this listing****",
        "PURE COLUMBIAN COCAINE\n\n"
        "DIRECT FROM SOUTH AMERICA\n"
        "This is our own sourced coke sent direct from South America,\n"
        "Comes in powder form only due to transport system\n"
        "Believe us or not you will see the quality when you try for "
        "yourself\n"
        "LIMITED STOCK but more on the way :)\n\n"
        "VERY OILY",
        [(0.5, 50.00), (1, 40.00), (2, 32.50), (3.5, 28.57), (7, 28.00),
         (14, 27.50), (28, 26.78), (56, 26.50), (84, 26.00), (125, 25.50)],
        "gr", "cat_stimulants"),
    # Stock reads "Unlimited" on the real page - a word, not a number, and
    # this field is only ever shown as text, so it goes in exactly as seen.
    # The tier list stops mid-screen ("from 28.00 gr @ £26.78") the same way
    # Afghani Black Glass's description does; there may be more below it.
    "st_ay_stamp": (
        "<b>“A-Y” Stamp Cocaine \U0001F1E8\U0001F1F4</b>",
        "Stimulants", "Unlimited", "4.9", 1205,
        "****Discount codes do not work with this listing****",
        "The finest high quality clean and uncut Columbian Cocaine "
        "❄️\nStraight off the brick.\n\n"
        "Tried & Tested ✅\n\n"
        "Free from amphet and benzos, enjoy a smooth Coke, with no burn "
        "or stuffy nose, no numb throat, jitters or sweats.\n\n"
        "We know what you look for and we know what you like. Smooth "
        "powder with a eurpohic high that will have you craving more!\n\n"
        "We have what you need ✅",
        [(0.25, 80.00), (0.5, 55.00), (1, 40.00), (2, 32.50), (3.5, 28.57),
         (7, 28.00), (14, 27.50), (28, 26.78)], "gr", "cat_stimulants"),
    "pv_pak_meth": (
        "<b>Pakistani Meth \U0001F1F5\U0001F1F0</b>", _STIM_METH_PATH,
        "279.75", "4.8", 75,
        "Pakistani Crystal Meth – Premium Mind-Stimulating Ice",
        "Experience the purest cognitive fuel available. Crafted with the "
        "highest purity and cleanliness, delivering exceptional clarity "
        "and mental stimulation.\n\n"
        "This premium formula provides long-lasting focus, razor-sharp "
        "alertness, and clean, jitter-free energy that keeps your mind "
        "operating at peak performance for hours. Whether youre tackling "
        "demanding work, intense partying, or need sustained mental "
        "endurance, this ice delivers the powerful stimulation",
        [(0.5, 37.50), (1, 35.00), (2, 32.50), (3.5, 30.00), (7, 27.50),
         (14, 25.00), (28, 22.50), (56, 20.00), (500, 15.00)], "gr",
        "cat_stim_meth"),

    # ---- FIRE SALE --------------------------------------------------------
    "fs_bicarb_crack": (
        "<b>Bicarbonate Crack Cocaine \U0001F1E8\U0001F1F4</b>",
        "\U0001F525 FIRE SALE \U0001F525", "445.00", "4.9", 330,
        "Discount code does not apply to fire sale items",
        "Go easy on this Bicarb washed Crack \U0001FAA8 Trust us it will "
        "take your breathaway - cooked in house, prepare to have your "
        "soul electrified with all the blissful spine-tingling sensations "
        "you desire and have come to expect, using only high quality "
        "Cocaine you can rest assured our product is certifiably strong "
        "and most importantly, clean. ✅\n\n"
        "26g RETURN ON 28g WASH",
        [(0.5, 50.00), (1, 47.50), (2, 45.00), (3.5, 42.50), (7, 37.50),
         (14, 35.00)], "gr", "cat_fire_sale"),
}


def _make_product_content(prefix):
    """Build the screen function for one CATALOGUE row."""
    def _content(chat_id=None):
        (title, path, stock, rating, reviews,
         blurb, desc, tiers, unit, back) = CATALOGUE[prefix]
        default = tiers[0][0]
        qty = get_quantity(chat_id) if chat_id else default
        price = PRICE_FNS[prefix](qty)
        body = "\n\n".join(p for p in (blurb, desc) if p)
        text = (f"{title}\n{path} • Stock {stock} • ★ {rating} ({reviews:,})\n\n"
                f"{body}\n\n{_tier_lines(tiers, unit)}")
        keyboard = build_mcw_keyboard(prefix, back, default, qty, price, chat_id,
                                      reviews=reviews, unit=unit)
        return text, keyboard, prefix
    _content.__name__ = f"{prefix}_content"
    return _content


def _register_catalogue():
    """Fold CATALOGUE into the same PRODUCTS / PRICE_FNS the old products use."""
    for prefix, row in CATALOGUE.items():
        title, _path, _stock, _rating, _reviews, _blurb, _desc, tiers, unit, _back = row
        default = tiers[0][0]
        PRICE_FNS[prefix] = (lambda t: lambda qty: tier_price(qty, t))(tiers)
        # strip the HTML and leading flags so the cart and wishlist read cleanly
        plain = re.sub(r"<[^>]+>", "", title).strip()
        plain = plain.lstrip("🇺🇸🇹🇭🇨🇦🇬🇧⭐🍯🤪☁️ ").strip()
        PRODUCTS[prefix] = (_make_product_content(prefix), default, default, plain)
        if unit != "gr":
            PRODUCT_UNITS[prefix] = unit


_register_catalogue()


def _make_pending_content(prefix, display_name, back):
    """A product with no price on file yet.

    Shows its name and flag and nothing else - no quantity stepper, no Add
    to Cart - so it cannot be bought before it has a real price, rather than
    selling at a fabricated one. Swap this for a real CATALOGUE/PRICE_FNS
    entry (see _register_catalogue above) once the price is known; nothing
    else about the product needs to change, the SECTIONS listing already
    points at this same prefix.
    """
    def _content(chat_id=None):
        text = (f"<b>{display_name}</b>\n\n"
                f"<i>Pricing coming soon.</i>")
        kb = types.InlineKeyboardMarkup()
        cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
        kb.row(types.InlineKeyboardButton("Back", callback_data=f"back:{back}"),
               types.InlineKeyboardButton(cl, callback_data="cart_main"))
        return text, kb, None
    _content.__name__ = f"{prefix}_content"
    return _content


# Everything that was waiting on a real price - the two Opiates, the two
# cocaines and Pakistani Meth in Stimulants, Bicarbonate Crack Cocaine in
# FIRE SALE - turned out to have one already; they are CATALOGUE rows above
# now, not here. This list (and _make_pending_content above) stays for the
# next product that gets added before its price does: add a row here with
# its prefix, display name and the category it should Back to, and it lists
# with a picture and "Pricing coming soon" until a real CATALOGUE row
# replaces it, same prefix, nothing else to change.
_PENDING_PRODUCTS = [
    # prefix, display name, primary back target
]
for _prefix, _name, _back in _PENDING_PRODUCTS:
    PRODUCTS[_prefix] = (_make_pending_content(_prefix, _name, _back), 1, 1, _name)

# =========================
# STOREFRONT MENUS
#
# Two tables drive every browsing screen:
#   SECTIONS  — a section and the products in it
#   MENU_TREE — a menu and the buttons on it
# The screen functions are generated from them, and the counts in the button
# labels are computed, so they cannot drift out of step with the contents.
# =========================

SECTIONS = {
    # HIGH for LESS
    "hfl_shake":    ("💷HIGH for LESS💷 → 🌻SHAKE",        "cat_hfl",
                     ["p_shake"]),
    "hfl_hash":     ("💷HIGH for LESS💷 → 🍫Hash",         "cat_hfl",
                     ["p_temple", "hfl_mazar"]),
    "hfl_bundles":  ("💷HIGH for LESS💷 → 🤝Bundles🤝",    "cat_hfl",
                     ["p_hash_bundle", "p_mixmatch", "p_3uk", "p_survbox", "hfl_full_melt_bundle"]),
    "hfl_offers":   ("🎒HIGH for LESS → ⭐OFFERS",         "cat_hfl",
                     ["p_offer_stardawg", "p_offer_gelato41", "p_offer_bluenerdz"]),
    "hfl_popcorn":  ("💷HIGH for LESS💷 → 🍿Popcorn buds", "cat_hfl",
                     ["p_bss40", "p_mim25", "p_stardawg_sm", "hfl_icc_smalls"]),

    # UK — section split is inferred from each strain's usual lineage
    "uk_hybrid":    ("🇬🇧UK → ⚖️HYBRID", "cat_uk",
                     ["flower_cherry", "flower_mccake", "flower_cream47", "flower_gelato",
                      "flower_wedding_cake", "flower_cherpaya"]),
    "uk_sativa":    ("🇬🇧UK → 🔼SATIVA", "cat_uk",
                     ["flower_amnesia", "flower_stardawg", "flower_frosted_lemon"]),
    "uk_indica":    ("🇬🇧UK → 🔽INDICA", "cat_uk",
                     ["flower_skywalker", "flower_criminal", "flower_godfather",
                      "flower_runtz"]),

    # USA
    "usa_hybrid":   ("🇺🇸USA → ⚖️HYBRID", "cat_usa",
                     ["usa_georgia_pie", "p_fire_runtz", "p_sour_runtz",
                      "usa_blueberry_bang", "usa_midnight_runtz", "usa_rockstar",
                      "usa_jet_fuel_gelato", "usa_cake_butter", "usa_omfg",
                      "usa_red_runtz", "usa_mac1"]),
    "usa_sativa":   ("🇺🇸USA → 🔼SATIVA", "cat_usa",
                     ["usa_mimosa", "usa_pineapple_punch", "usa_wedding_crasher",
                      "usa_cool_mint", "usa_banana_cooler", "usa_alpine_guava"]),
    "usa_indica":   ("🇺🇸USA → 🔽INDICA", "cat_usa",
                     ["usa_wc_pink", "usa_gummy_buns", "usa_death_bubba",
                      "usa_blue_lobster", "usa_animal_cookies",
                      "p_dosi", "p_iceage", "p_twix"]),

    # CANADA
    "can_indica":   ("🇨🇦CANADA → 🔽INDICA", "cat_canada", ["p_icecream"]),

    # THAILAND
    "thai_sativa":  ("🇹🇭THAILAND → 🔼SATIVA", "cat_thai", ["thai_tropical_runtz"]),
    "thai_hybrid":  ("🇹🇭THAILAND → ⚖️HYBRID", "cat_thai", ["thai_sunset_sherbet"]),

    # EXTRACTS, VAPES & EDIBLES
    "evd_extracts": ("🧡Extracts, vapes & edibles → 🍯EXTRACTS", "cat_evd",
                     ["ext_tangie", "ext_gumbo", "ext_grape_gas", "ext_runtz_scat_cookies",
                      "ext_caramel_cream", "ext_yahmi", "ext_rasberry_cough", "ext_tyson_glue",
                      "ext_lemon_cherry_gelato", "ext_cream_sherbet", "ext_bloody_mary",
                      "ext_stoney_og", "p_gelato_ext", "p_havana", "p_airheadz", "p_mochi"]),

    # HASH
    "hash_full_melt": ("🍫HASH → ⭐Premium Blend 24k Full Melt", "hash",
                       ["hash_godfather_db", "hash_guava_pbb", "hash_slurricane", "hash_pink_kush"]),
    "evd_vapes":    ("🧡Extracts, vapes & edibles → ☁️VAPES", "cat_evd",
                     ["p_vape1g"]),
    "evd_edibles":  ("🧡Extracts, vapes & edibles → 🤪EDIBLES", "cat_evd",
                     ["edi_funnies"]),

    # PUFFCO holds a single product, so it is a section hanging straight off
    # the root menu rather than a menu with one child in it.
    "cat_puffco":   ("⭐PUFFCO⭐10 G EXTRACTS BUNDLE", "listings",
                     ["p_puffco_bundle"]),

    # Private re-lists several products already sold elsewhere (same prefix,
    # not a second copy) alongside Pakistani Meth, which also lives under
    # Stimulants → Methamphetamine. Defined here, ahead of the categories
    # below that list these same products, deliberately: build_mcw_keyboard
    # sends a product's own Back button to whichever SECTIONS entry listed
    # it *last*, so if this sat after them instead, every shared product's
    # Back would quietly go to Private no matter which screen it was opened
    # from. Putting Private first instead means the more specific category
    # always wins that race, and Private - which nothing else points at -
    # never does.
    "cat_private": ("<b>Private</b>", "listings",
                    ["st_pure_colombian", "pv_pak_meth", "op_pak4_china_white",
                     "op_afghan_black", "uk_special_queen", "st_ay_stamp",
                     "fs_bicarb_crack", "uk_strawberry_cheesecake"]),

    # ---- Cannabis → UK Grown / Hash ----------------------------------------
    # _make_section_content prepends "Category: " itself; the caption here
    # carries the rest, including the "Cannabis → " breadcrumb these two
    # screens show (the other four below sit straight off the root, so they
    # need no breadcrumb).
    "cat_cannabis_uk": ("<b>Cannabis → UK Grown \U0001F1EC\U0001F1E7</b>",
                        "cat_cannabis",
                        ["uk_special_queen", "uk_cereal_milk",
                         "uk_strawberry_cheesecake"]),
    "cat_cannabis_hash": ("<b>Cannabis → Hash</b>", "cat_cannabis",
                          ["hash_cali_plates"]),

    # ---- Dissociatives → Ketamine / Ecstasy → MDMA -------------------------
    "cat_dissoc_ketamine": ("<b>Ketamine</b>", "cat_dissoc",
                            ["ds_shard_ketamine", "ds_rock_ketamine"]),
    "cat_ecstasy_mdma":    ("<b>MDMA</b>", "cat_ecstasy",
                            ["ec_8ball_250", "ec_dutch_mdma"]),

    # ---- Stimulants → Methamphetamine --------------------------------------
    # Reached from the hand-written stimulants_content below, not listed as
    # a SECTIONS button anywhere - cat_stimulants itself is not a plain
    # SECTIONS entry (see the note by stimulants_content for why).
    "cat_stim_meth": ("<b>" + _STIM_METH_PATH + "</b>", "cat_stimulants",
                      ["pv_pak_meth"]),

    # ---- the four flat leaf categories off the root ------------------------
    "cat_opiates": ("<b>Opiates</b>", "listings",
                    ["op_afghan_black", "op_pak4_china_white"]),
    # The auto-rendered screen this would produce is never shown - see
    # stimulants_content, which replaces it in STORE_SCREENS below - this
    # entry exists only so _menu_count("cat_stimulants") still reads 3 on
    # the root "listings" screen's "Stimulants (3)" button.
    "cat_stimulants": ("<b>Stimulants</b>", "listings",
                       ["cat_stim_meth", "st_pure_colombian", "st_ay_stamp"]),
    "cat_fire_sale": ("<b>\U0001F525 FIRE SALE \U0001F525</b>", "listings",
                      ["fs_bicarb_crack"]),
}

# menu key: (caption, back callback, [(label, target), ...])
MENU_TREE = {
    "listings": ("Select a product category to fill your shopping basket", "main_back", [
        ("Cannabis",       "cat_cannabis"),
        ("Dissociatives",  "cat_dissoc"),
        ("Ecstasy",        "cat_ecstasy"),
        ("Opiates",        "cat_opiates"),
        ("Stimulants",     "cat_stimulants"),
        ("\U0001F525 FIRE SALE \U0001F525", "cat_fire_sale"),
        ("Private",        "cat_private"),
    ]),
    "cat_cannabis": ("Category: <b>Cannabis</b>", "listings", [
        ("UK Grown \U0001F1EC\U0001F1E7", "cat_cannabis_uk"),
        ("Hash",                         "cat_cannabis_hash"),
    ]),
    "cat_dissoc": ("Category: <b>Dissociatives</b>", "listings", [
        ("Ketamine", "cat_dissoc_ketamine"),
    ]),
    "cat_ecstasy": ("Category: <b>Ecstasy</b>", "listings", [
        ("MDMA", "cat_ecstasy_mdma"),
    ]),
    # The old demo tree this file came with - HIGH for LESS / UK / USA /
    # CANADA / THAILAND / Extracts-vapes-edibles / HASH, and everything under
    # them - is unreachable now that "listings" points at the real
    # categories above instead. Left in place rather than torn out of a file
    # this size; ask if you want it deleted outright.
    "cat_hfl": ("Category: 💷HIGH for LESS💷", "listings", [
        ("🌻 SHAKE",        "hfl_shake"),
        ("🍫 Hash",         "hfl_hash"),
        ("🤝 Bundles🤝",    "hfl_bundles"),
        ("⭐ OFFERS",       "hfl_offers"),
        ("🍿 Popcorn buds", "hfl_popcorn"),
    ]),
    "cat_uk": ("Category: 🇬🇧UK", "listings", [
        ("⚖️ HYBRID", "uk_hybrid"),
        ("🔼 SATIVA", "uk_sativa"),
        ("🔽 INDICA", "uk_indica"),
    ]),
    "cat_usa": ("Category: 🇺🇸USA", "listings", [
        ("⚖️ HYBRID", "usa_hybrid"),
        ("🔼 SATIVA", "usa_sativa"),
        ("🔽 INDICA", "usa_indica"),
    ]),
    "cat_canada": ("Category: 🇨🇦CANADA", "listings", [
        ("🔽 INDICA", "can_indica"),
    ]),
    "cat_thai": ("Category: 🇹🇭THAILAND", "listings", [
        ("🔼 SATIVA", "thai_sativa"),
        ("⚖️ HYBRID", "thai_hybrid"),
    ]),
    "cat_evd": ("Category: 🧡Extracts, vapes & edibles", "listings", [
        ("🍯 EXTRACTS", "evd_extracts"),
        ("☁️ VAPES",    "evd_vapes"),
        ("🤪 EDIBLES",  "evd_edibles"),
    ]),
    "hash": ("Category: 🍫HASH", "listings", [
        ("⭐ Premium Blend 24k Full Melt", "hash_full_melt"),
    ]),
}


# Which section each product is filed under, so Back always has somewhere real
# to go and never has to trust the target hardcoded in an old product screen.
# A SECTIONS list can name something that is not a real product - cat_stimulants
# below lists "cat_stim_meth" (a category, not a product) purely so
# _menu_count sees the right length for its root-menu button - so this skips
# anything _make_section_content would have skipped too, the same guard it
# already applies when it builds that screen's buttons.
PRODUCT_SECTION = {p: key for key, (_c, _b, ps) in SECTIONS.items() for p in ps
                   if p in PRODUCTS}


def _display_label(prefix):
    """The product's own headline, minus the HTML — keeps its flags and emoji.

    Taken from the product screen itself rather than a second list of names, so
    a button can never show different decoration from the page it opens.
    """
    try:
        head = PRODUCTS[prefix][0](None)[0].split("\n", 1)[0]
    except Exception:
        return PRODUCTS[prefix][3]
    label = re.sub(r"<[^>]+>", "", head).strip()
    return label or PRODUCTS[prefix][3]


PRODUCT_LABELS = {}


def _menu_count(target):
    """How many products sit under a button, counting through sub-menus."""
    if target in SECTIONS:
        return len(SECTIONS[target][2])
    if target in MENU_TREE:
        return sum(_menu_count(t) for _label, t in MENU_TREE[target][2])
    return 0


def _make_menu_content(key):
    def _content(chat_id=None):
        caption, back, buttons = MENU_TREE[key]
        kb = types.InlineKeyboardMarkup()
        for label, target in buttons:
            kb.add(types.InlineKeyboardButton(f"{label} ({_menu_count(target)})",
                                              callback_data=target))
        cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
        kb.row(types.InlineKeyboardButton("Back", callback_data=f"back:{back}"),
               types.InlineKeyboardButton(cl, callback_data="cart_main"))
        return caption, kb
    _content.__name__ = f"{key}_content"
    return _content


def _make_section_content(key):
    def _content(chat_id=None):
        caption, back, prefixes = SECTIONS[key]
        kb = types.InlineKeyboardMarkup()
        for p in prefixes:
            if p in PRODUCTS:
                kb.add(types.InlineKeyboardButton(PRODUCT_LABELS.get(p, PRODUCTS[p][3]),
                                                  callback_data=p))
        cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
        kb.row(types.InlineKeyboardButton("Back", callback_data=f"back:{back}"),
               types.InlineKeyboardButton(cl, callback_data="cart_main"))
        return f"Category: {caption}", kb
    _content.__name__ = f"{key}_content"
    return _content


def stimulants_content(chat_id=None):
    """Stimulants: one button to the Methamphetamine sub-category, then two
    real products directly. Hand-written because neither generic factory
    renders that mix on one screen - _make_menu_content always adds a count
    to every button (right for "Methamphetamine", wrong for the two
    cocaines, which carry none on the real page), and _make_section_content
    never adds one (right for the cocaines, wrong for Methamphetamine).
    """
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(
        f"Methamphetamine ({_menu_count('cat_stim_meth')})",
        callback_data="cat_stim_meth"))
    for p in ("st_pure_colombian", "st_ay_stamp"):
        kb.add(types.InlineKeyboardButton(PRODUCT_LABELS.get(p, PRODUCTS[p][3]),
                                          callback_data=p))
    cl = cart_button(chat_id) if chat_id else "🛒 £0.00"
    kb.row(types.InlineKeyboardButton("Back", callback_data="back:listings"),
           types.InlineKeyboardButton(cl, callback_data="cart_main"))
    return "Category: <b>Stimulants</b>", kb


# Every browsing screen, wired to its own picture in images/<name>.jpg.
STORE_SCREENS = {}
for _key in MENU_TREE:
    STORE_SCREENS[_key] = (_make_menu_content(_key), _key)
for _key in SECTIONS:
    STORE_SCREENS[_key] = (_make_section_content(_key), _key)
# The root keeps the picture it already had.
STORE_SCREENS["listings"] = (STORE_SCREENS["listings"][0], "listings")
# Overrides the generic SECTIONS rendering above - see stimulants_content.
STORE_SCREENS["cat_stimulants"] = (stimulants_content, "cat_stimulants")

# Built once now that every product is registered, so the section buttons carry
# the same flags and emoji as the product screens they open.
PRODUCT_LABELS.update({p: _display_label(p) for p in PRODUCT_SECTION})


# The browsing screens above are the ones the buttons open; SCREEN_IMAGES is
# the name the handler looks them up by.
SCREEN_IMAGES = STORE_SCREENS

# =========================
# START / ADMIN COMMANDS
# =========================

@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    user_last_msg_id[chat_id] = message.message_id
    user = message.from_user
    db_register_user(chat_id, user.username, user.first_name)
    waiting_for_contact.discard(chat_id)
    waiting_for_announcement_users.discard(chat_id)
    waiting_for_passphrase.discard(chat_id)
    if chat_id in verified_users:
        # Returning user — confirm the stored phrase, then straight to the menu.
        text, keyboard = phrase_check_content(chat_id, yes_cb="phrase_recheck_yes")
        bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
    else:
        # Everyone, admins included, sets a phrase before reaching the menu.
        bot.send_message(chat_id, f"Welcome, <b>{_esc(user.first_name or 'there')}</b> !", parse_mode="HTML")
        text, keyboard = phrase_intro_content()
        bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")

@bot.message_handler(commands=['admin'])
def admin_command(message):
    chat_id = message.chat.id
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "⛔ Unauthorized.")
        return
    text, keyboard = admin_panel_content()
    bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")

# =========================
# TEXT INPUT HANDLER
# =========================

@bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker'])
def handle_text_input(message):
    chat_id = message.chat.id
    user_last_msg_id[chat_id] = message.message_id
    user = message.from_user
    db_register_user(chat_id, user.username, user.first_name)

    def _relay_to_user(target_user, src_msg_id):
        if message.text:
            reply_text = message.text
        elif message.photo:
            reply_text = "[photo]" + (f": {message.caption}" if message.caption else "")
        elif message.video:
            reply_text = "[video]" + (f": {message.caption}" if message.caption else "")
        elif message.document:
            reply_text = "[file]" + (f": {message.caption}" if message.caption else "")
        elif message.voice:
            reply_text = "[voice message]"
        elif message.sticker:
            reply_text = "[sticker]"
        else:
            reply_text = message.caption or "[media]"
        bot.send_message(target_user, "💬 <b>Reply from seller:</b>", parse_mode="HTML",
                         reply_to_message_id=user_warning_msg_id.get(target_user))
        # copy from whichever admin is replying, not just the primary one
        bot.copy_message(target_user, chat_id, src_msg_id)
        action_kb = types.InlineKeyboardMarkup()
        action_kb.add(types.InlineKeyboardButton("↩️ Reply Vendor", callback_data="contact_reply_vendor"))
        action_kb.row(
            types.InlineKeyboardButton("Close Chat", callback_data="contact_close"),
            types.InlineKeyboardButton("Clear Chat", callback_data="contact_clear")
        )
        waiting_for_contact.add(target_user)
        bot.send_message(target_user, "↩️ Type a message to reply back to the seller.",
                         reply_markup=action_kb)
        db_add_chat_message(target_user, reply_text, "seller")

    if chat_id in waiting_for_passphrase:
        phrase = (message.text or "").strip()
        if not phrase:
            bot.send_message(chat_id, "❌ Send your phrase as a plain text message.")
            return
        if not PHRASE_MIN_LEN <= len(phrase) <= PHRASE_MAX_LEN:
            bot.send_message(
                chat_id,
                f"❌ The phrase must be {PHRASE_MIN_LEN}–{PHRASE_MAX_LEN} characters. "
                f"Yours is {len(phrase)}. Please send it again."
            )
            return
        waiting_for_passphrase.discard(chat_id)
        verified_users.add(chat_id)
        user_secret_phrase[chat_id] = phrase
        db_set_user_verified(chat_id, phrase)
        try: bot.delete_message(chat_id, message.message_id)
        except: pass
        bot.send_message(chat_id, PHRASE_SAVED_TEXT, parse_mode="HTML")
        text, keyboard = phrase_check_content(chat_id)
        bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
        return

    if chat_id in ADMIN_IDS and chat_id in waiting_for_admin_reply:
        target_user = waiting_for_admin_reply.pop(chat_id)
        try:
            _relay_to_user(target_user, message.message_id)
            try: bot.delete_message(chat_id, message.message_id)
            except: pass
            bot.send_message(chat_id, "✅ Reply delivered to user.")
        except Exception as e:
            logging.warning(f"Failed to send admin reply: {e}")
            bot.send_message(chat_id, f"❌ Failed to deliver: {e}")
        return

    if chat_id in ADMIN_IDS and message.reply_to_message:
        replied_to_id = message.reply_to_message.message_id
        target_user = admin_reply_map.get(replied_to_id)
        if target_user:
            try:
                _relay_to_user(target_user, message.message_id)
                bot.send_message(chat_id, "✅ Reply delivered to user.", reply_to_message_id=message.message_id)
            except Exception as e:
                logging.warning(f"Failed to relay admin reply: {e}")
                bot.send_message(chat_id, f"❌ Failed to deliver reply: {e}")
            return

    if chat_id in waiting_for_admin_review:
        waiting_for_admin_review.discard(chat_id)
        raw = (message.text or "").strip()
        lines = raw.split("\n", 2)
        err = None
        header_match = None
        if len(lines) < 3:
            err = "Need at least 3 lines (header, product(s), review text)."
        else:
            header_match = _ADMIN_REVIEW_HEADER_RE.match(lines[0].strip())
            if not header_match:
                err = ("Line 1 must look like: "
                       "today at 08:58 ★★★★★ — £77.23")
        if err:
            bot.send_message(chat_id, f"❌ {err}\nSend /start to go back.")
            return
        date_part, time_part, stars_str, amount_digits = header_match.groups()
        review = {
            "date":     f"{date_part} at {time_part}",
            "stars":    stars_str.count("★"),
            "amount":   f"£{amount_digits}",
            "products": lines[1].strip(),
            "text":     lines[2].strip(),
        }
        pending_admin_review[chat_id] = review
        star_str = stars_str
        preview = (
            f"⭐ <b>Review Preview:</b>\n\n"
            f"{review['date']} {star_str} — {review['amount']}\n"
            f"{review['products']}\n"
            f"⤷ {review['text']}"
        )
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("✅ Add to Ratings", callback_data="admin_confirm_rating"),
            types.InlineKeyboardButton("❌ Cancel", callback_data="admin_cancel_rating")
        )
        bot.send_message(chat_id, preview, reply_markup=kb, parse_mode="HTML")
        return

    if chat_id in waiting_for_announcement_users:
        waiting_for_announcement_users.discard(chat_id)
        if message.photo:
            payload = {"type": "photo", "file_id": message.photo[-1].file_id,
                       "text": message.caption or "", "product_key": None}
        elif message.video:
            payload = {"type": "video", "file_id": message.video.file_id,
                       "text": message.caption or "", "product_key": None}
        elif message.text:
            payload = {"type": "text", "text": message.text, "product_key": None}
        else:
            bot.send_message(chat_id, "❌ Only text, photo, or video is supported.")
            txt, kb = admin_panel_content()
            bot.send_message(chat_id, txt, reply_markup=kb, parse_mode="HTML")
            return
        try: bot.delete_message(chat_id, message.message_id)
        except: pass
        pending_announcements[chat_id] = payload
        waiting_for_announcement_product.add(chat_id)
        bot.send_message(chat_id, "🔗 Link this announcement to a product?",
                         reply_markup=announcement_product_keyboard())
        return

    if chat_id in waiting_for_contact:
        if message.text:
            display_text = message.text
        elif message.photo:
            display_text = "[photo]" + (f": {message.caption}" if message.caption else "")
        elif message.video:
            display_text = "[video]" + (f": {message.caption}" if message.caption else "")
        elif message.document:
            display_text = "[file]" + (f": {message.caption}" if message.caption else "")
        elif message.voice:
            display_text = "[voice message]"
        elif message.sticker:
            display_text = "[sticker]"
        else:
            display_text = "[media]"
        try: bot.delete_message(chat_id, message.message_id)
        except: pass
        username = f"@{user.username}" if user.username else "no username"
        reply_kb = types.InlineKeyboardMarkup()
        reply_kb.add(types.InlineKeyboardButton(
            "↩️ Reply User", callback_data=f"admin_reply_to_{chat_id}"
        ))
        try:
            if message.text:
                notify = (
                    f"📩 <b>Customer message</b>\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"👤 {user.first_name} {user.last_name or ''}  {username}\n"
                    f"🆔 <code>{chat_id}</code>\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"💬 {message.text}"
                )
                notify_admins(notify, reply_markup=reply_kb, track_user=chat_id)
            else:
                header = (
                    f"📩 <b>Customer message</b>\n"
                    f"━━━━━━━━━━━━━━━\n"
                    f"👤 {user.first_name} {user.last_name or ''}  {username}\n"
                    f"🆔 <code>{chat_id}</code>"
                )
                forward_media_to_admins(chat_id, message.message_id, header,
                                        reply_markup=reply_kb, track_user=chat_id)
        except Exception as e:
            logging.warning(f"Failed to forward to admin: {e}")
        db_add_chat_message(chat_id, display_text, "user")
        conf = bot.send_message(chat_id, "✅ Message sent to seller.",
                                reply_to_message_id=user_warning_msg_id.get(chat_id))
        def _del(m=conf):
            time.sleep(3)
            try: bot.delete_message(chat_id, m.message_id)
            except: pass
        threading.Thread(target=_del, daemon=True).start()
        return

    if chat_id in waiting_for_qty:
        prefix, prompt_mid, product_mid = waiting_for_qty[chat_id]
        try:
            qty = float(message.text.strip())
            if qty <= 0: raise ValueError
            set_quantity(chat_id, qty)
            del waiting_for_qty[chat_id]
            for mid in [prompt_mid, message.message_id]:
                try: bot.delete_message(chat_id, mid)
                except: pass
            if prefix in PRODUCTS:
                content_fn = PRODUCTS[prefix][0]
                text, keyboard, image_key = content_fn(chat_id)
                if image_key:
                    photo_id = get_product_image(image_key, chat_id)
                    if photo_id:
                        bot.send_photo(chat_id, photo_id, caption=text, reply_markup=keyboard, parse_mode="HTML",
                                       reply_to_message_id=user_warning_msg_id.get(chat_id))
                        return
                edit_text(chat_id, product_mid, text, keyboard)
        except ValueError:
            try: bot.delete_message(chat_id, message.message_id)
            except: pass
            bot.send_message(chat_id, "❌ Invalid quantity. Please enter a positive number:",
                             reply_to_message_id=user_warning_msg_id.get(chat_id))
        return

    if chat_id in waiting_for_discount:
        discount_msg_id = waiting_for_discount[chat_id]
        user_discount[chat_id] = message.text.strip().upper()
        del waiting_for_discount[chat_id]
        try: bot.delete_message(chat_id, message.message_id)
        except: pass
        text, keyboard = checkout_content(chat_id)
        edit_text(chat_id, discount_msg_id, text, keyboard)
        return

    if chat_id in waiting_for_address:
        prompt_mid = waiting_for_address[chat_id]
        address_text = message.text.strip() if message.text else ""
        try: bot.delete_message(chat_id, message.message_id)
        except: pass
        if len(address_text) < 8:
            err_text = ("Check that the delivery address is correct and try again.\n\n"
                        "<b>PLEASE REMEMBER TO SET THE EXPIRY DATE OF THE LINK TO A MINIMUM OF 7 DAYS "
                        "TO ALLOW AMPLE TIME FOR PROCESSING YOUR ORDER</b>")
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("Try again", callback_data="checkout_address_retry"))
            kb.add(types.InlineKeyboardButton("Back to Order", callback_data="checkout"))
            edit_text(chat_id, prompt_mid, err_text, kb)
            waiting_for_address.pop(chat_id, None)
        else:
            user_address[chat_id] = address_text
            del waiting_for_address[chat_id]
            try: bot.delete_message(chat_id, prompt_mid)
            except: pass
            conf_text = ("🎉 Your address has been encrypted using the seller's public key and saved. "
                         "The address will be revealed to the seller after the order is paid for.")
            kb = types.InlineKeyboardMarkup()
            kb.row(
                types.InlineKeyboardButton("Check Delivery Address", callback_data="checkout_check_address"),
                types.InlineKeyboardButton("Remove Delivery Address", callback_data="checkout_remove_address")
            )
            kb.add(types.InlineKeyboardButton("Continue checkout", callback_data="checkout_continue"))
            bot.send_message(chat_id, conf_text, reply_markup=kb,
                             reply_to_message_id=user_warning_msg_id.get(chat_id))
        return

# =========================
# BUTTON HANDLER
# =========================

@bot.callback_query_handler(func=lambda call: True)
def buttons(call):
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    user_last_msg_id[chat_id] = message_id
    data = call.data
    is_photo = call.message.content_type == "photo"

    # Add to Cart answers its own callback query further down, with a pop-up
    # alert instead of this default empty answer - a callback query can only
    # be answered once, so it must not be answered here too.
    if not data.endswith("_add_to_cart"):
        bot.answer_callback_query(call.id)


    def nav_text(text, keyboard):
        go_text_screen(chat_id, message_id, text, keyboard, is_photo)

    def nav_screen(text, keyboard, image_key):
        """Same as nav_text, but shows images/<image_key> when that file exists."""
        open_screen(chat_id, message_id, text, keyboard, is_photo, image_key)

    # --- SECRET PHRASE ONBOARDING ---
    if data == "phrase_set_own":
        waiting_for_passphrase.add(chat_id)
        text, keyboard = phrase_entry_content()
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=keyboard, parse_mode="HTML")
        except: pass
        return

    if data == "phrase_about":
        # Sent as a reply to the screen it was opened from, so that screen stays visible underneath.
        text, keyboard = phrase_about_content()
        bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML",
                         reply_to_message_id=message_id)
        return

    if data == "phrase_about_ok":
        try: bot.delete_message(chat_id, message_id)
        except: pass
        # The screen the About was opened from is untouched, so it is on show again.
        return

    if data == "phrase_check_no":
        # Deliberately does nothing — the user stays on the confirmation screen.
        return

    if data == "phrase_check_yes":
        text, keyboard = phrase_private_warning_content()
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=keyboard, parse_mode="HTML")
        except: pass
        return

    if data == "phrase_agree":
        text, keyboard = phrase_groupchat_content()
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=keyboard, parse_mode="HTML")
        except: pass
        return

    if data == "phrase_groups_ok":
        try: bot.delete_message(chat_id, message_id)
        except: pass
        show_main_with_warning(chat_id)
        return

    if data == "phrase_recheck_yes":
        # Returning user confirmed their phrase — skip the onboarding notices.
        try: bot.delete_message(chat_id, message_id)
        except: pass
        show_main_with_warning(chat_id)
        return

    if data == "close_announcement":
        try: bot.delete_message(chat_id, message_id)
        except: pass
        return

    if data.startswith("admin_reply_to_"):
        if chat_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True); return
        target_uid = int(data.split("admin_reply_to_")[1])
        waiting_for_admin_reply[chat_id] = target_uid
        cancel_kb = types.InlineKeyboardMarkup()
        cancel_kb.add(types.InlineKeyboardButton("❌ Cancel", callback_data="admin_cancel_reply"))
        bot.send_message(chat_id, "✏️ Type your reply and send it:", reply_markup=cancel_kb)
        return

    if data == "admin_cancel_reply":
        if chat_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True); return
        waiting_for_admin_reply.pop(chat_id, None)
        try: bot.delete_message(chat_id, message_id)
        except: pass
        return

    if data == "admin_announce":
        if chat_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True); return
        waiting_for_announcement_users.add(chat_id)
        try: bot.delete_message(chat_id, message_id)
        except: pass
        bot.send_message(chat_id, "📢 Send your announcement (text, photo with caption, or video with caption):")
        return

    if data == "announce_no_product":
        if chat_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True); return
        waiting_for_announcement_product.discard(chat_id)
        payload = pending_announcements.get(chat_id)
        if not payload:
            bot.send_message(chat_id, "❌ Session expired. Start over."); return
        try: bot.delete_message(chat_id, message_id)
        except: pass
        send_announce_preview(chat_id, payload)
        return

    if data.startswith("announce_product_"):
        if chat_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True); return
        waiting_for_announcement_product.discard(chat_id)
        payload = pending_announcements.get(chat_id)
        if not payload:
            bot.send_message(chat_id, "❌ Session expired. Start over."); return
        payload["product_key"] = data[len("announce_product_"):]
        try: bot.delete_message(chat_id, message_id)
        except: pass
        send_announce_preview(chat_id, payload)
        return

    if data == "admin_confirm_announce":
        if chat_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True); return
        payload = pending_announcements.pop(chat_id, None)
        if not payload:
            bot.send_message(chat_id, "❌ No announcement found."); return
        try: bot.delete_message(chat_id, message_id)
        except: pass
        bot.send_message(chat_id, f"📤 Sending to {db_user_count() - 1} users...")
        threading.Thread(target=broadcast_announcement, args=(payload, chat_id), daemon=True).start()
        return

    if data == "admin_cancel_announce":
        if chat_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True); return
        pending_announcements.pop(chat_id, None)
        try: bot.delete_message(chat_id, message_id)
        except: pass
        text, keyboard = admin_panel_content()
        bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
        return

    if data == "admin_add_rating":
        if chat_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True); return
        waiting_for_admin_review.add(chat_id)
        try: bot.delete_message(chat_id, message_id)
        except: pass
        bot.send_message(chat_id,
            "⭐ <b>Add a Rating</b>\n\n"
            "Send the review in this exact format:\n\n"
            "<code>today at 08:58 ★★★★★ — £77.23\n"
            "Strawberry Cheesecake🍓🧀\n"
            "NDD, was packed really well, no chance of it smelling through "
            "the packaging. Bud has good bag appeal, terpy smoke, really "
            "good bud for the price</code>\n\n"
            "Line 1: Date/time, stars (★★★★★), amount - exactly like a "
            "review already looks on the ratings page\n"
            "Line 2: Product(s)\n"
            "Line 3+: Review text",
            parse_mode="HTML")
        return

    if data == "admin_confirm_rating":
        if chat_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True); return
        review = pending_admin_review.pop(chat_id, None)
        if not review:
            bot.send_message(chat_id, "❌ No review pending."); return
        try: bot.delete_message(chat_id, message_id)
        except: pass
        db_add_review(review["date"], review["stars"], review["amount"],
                      review["products"], review["text"])
        bot.send_message(chat_id, f"✅ Review added! It now appears on page 1 of ratings.")
        return

    if data == "admin_cancel_rating":
        if chat_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True); return
        pending_admin_review.pop(chat_id, None)
        try: bot.delete_message(chat_id, message_id)
        except: pass
        text, keyboard = admin_panel_content()
        bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
        return

    if data == "admin_users":
        if chat_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True); return
        pending, completed, expired = db_get_order_status_counts()
        users = db_get_all_users()
        text = (f"👥 <b>Users ({db_user_count()})</b>\n"
                f"📦 Orders: {db_order_count()} — 🕐 {pending} pending / ✅ {completed} done / ❌ {expired} expired\n\n")
        for uid, username, first_name in users[:50]:
            name = first_name or "—"
            uname = f"@{username}" if username else "no username"
            text += f"• {name} | {uname} | <code>{uid}</code>\n"
        if len(users) > 50:
            text += f"\n<i>…and {len(users) - 50} more</i>"
        keyboard = types.InlineKeyboardMarkup()
        keyboard.add(types.InlineKeyboardButton("⬅️ Back to Panel", callback_data="admin_back"))
        try: bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=keyboard, parse_mode="HTML")
        except: bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
        return

    if data == "admin_orders":
        if chat_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True); return
        text, keyboard = admin_orders_content()
        try: bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=keyboard, parse_mode="HTML")
        except: bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
        return

    if data == "admin_back":
        if chat_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True); return
        text, keyboard = admin_panel_content()
        try: bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=keyboard, parse_mode="HTML")
        except: bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
        return

    if data == "admin_close":
        if chat_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "⛔ Unauthorized.", show_alert=True); return
        try: bot.delete_message(chat_id, message_id)
        except: pass
        return

    if data.startswith("remove_"):
        remove_from_cart(chat_id, int(data.split("_")[1]))
        nav_screen(*cart_content(chat_id), "cart"); return

    if data.startswith("order_refresh_"):
        invoice_num = int(data.split("_")[2])
        order = db_get_order(invoice_num)
        if order:
            nav_screen(*payment_pending_content(order), "order_detail")
        return

    if data.startswith("order_notes_"):
        invoice_num = int(data.split("_")[2])
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Cancel", callback_data=f"order_{invoice_num}"))
        nav_text("✏️ Type your note and send it:", kb)
        waiting_for_qty[chat_id] = (f"__notes_{invoice_num}", message_id, message_id)
        return

    if data.startswith("order_"):
        nav_screen(*order_detail_content(chat_id, int(data.split("_")[1])), "order_detail")
        return

    if data.startswith("qr_"):
        invoice_num = int(data.split("_")[1])
        order = db_get_order(invoice_num)
        if order:
            qr_image = generate_qr_image(order["crypto_address"])
            caption = (f"Payment for <b>Invoice {invoice_num}</b> - @YourBotUsername\n\n"
                       f"Scan the QR code with your favourite wallet for easier payment.")
            del_kb = types.InlineKeyboardMarkup()
            del_kb.add(types.InlineKeyboardButton("🗑 Delete QR Code", callback_data=f"delqr_{invoice_num}"))
            try: bot.delete_message(chat_id, message_id)
            except: pass
            bot.send_photo(chat_id, qr_image, caption=caption, reply_markup=del_kb, parse_mode="HTML")
        return

    if data.startswith("delqr_"):
        invoice_num = int(data.split("_")[1])
        try: bot.delete_message(chat_id, message_id)
        except: pass
        order = db_get_order(invoice_num)
        if order:
            text, keyboard = payment_pending_content(order)
            bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
        return

    if data == "contact":
        waiting_for_contact.add(chat_id)
        nav_screen(*contact_content(chat_id), "contact")
        return

    if data == "contact_close":
        waiting_for_contact.discard(chat_id)
        nav_screen(*closed_chat_content(), "contact_closed")
        return

    if data == "contact_reopen":
        waiting_for_contact.add(chat_id)
        nav_screen(*contact_content(chat_id), "contact")
        return

    if data == "contact_history":
        nav_screen(*chat_history_content(chat_id), "contact")
        return

    if data == "contact_reply_vendor":
        waiting_for_contact.add(chat_id)
        cancel_kb = types.InlineKeyboardMarkup()
        cancel_kb.add(types.InlineKeyboardButton("❌ Cancel", callback_data="contact_cancel_reply"))
        bot.send_message(chat_id, "✏️ Type your reply to the vendor and send it:", reply_markup=cancel_kb)
        return

    if data == "contact_cancel_reply":
        try: bot.delete_message(chat_id, message_id)
        except: pass
        return

    if data == "contact_clear":
        waiting_for_contact.discard(chat_id)
        db_clear_chat_history(chat_id)
        try: bot.delete_message(chat_id, message_id)
        except: pass
        show_main_with_warning(chat_id)
        return

    if data == "checkout":
        waiting_for_address.pop(chat_id, None)
        user_pending_invoice.setdefault(chat_id, generate_invoice())
        nav_text(*checkout_content(chat_id)); return
    if data == "checkout_discount":
        nav_text(*discount_content())
        waiting_for_discount[chat_id] = message_id; return
    if data == "checkout_payment": nav_text(*payment_content()); return

    if data == "checkout_address":
        addr_text, addr_kb = address_content()
        go_text_screen(chat_id, message_id, addr_text, addr_kb, is_photo)
        waiting_for_address[chat_id] = message_id
        return

    if data == "checkout_why_safe":
        text = ("With PGP encryption, only the intended recipient (the shop owner) possessing the "
                "private key can decrypt and access your address. This provides end-to-end encryption, "
                "preventing any middlemen or service providers from accessing your private information.")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Delete Message", callback_data="delete_this_msg"))
        bot.send_message(chat_id, text, reply_markup=kb, reply_to_message_id=message_id)
        return

    if data == "checkout_pgp_key":
        _send_pgp(chat_id)
        return

    if data == "delete_this_msg":
        try: bot.delete_message(chat_id, message_id)
        except: pass
        return

    if data == "delete_order":
        text = "Are you sure you want to delete this order? This action cannot be undone."
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Confirm Deletion", callback_data="delete_order_confirm"))
        kb.add(types.InlineKeyboardButton("Cancel", callback_data="checkout"))
        nav_text(text, kb); return

    if data == "delete_order_confirm":
        db_clear_cart(chat_id)
        user_discount.pop(chat_id, None)
        user_payment.pop(chat_id, None)
        user_address.pop(chat_id, None)
        user_delivery.pop(chat_id, None)
        user_pending_invoice.pop(chat_id, None)
        nav_screen(*main_menu_content(chat_id), "menu"); return

    if data == "checkout_address_retry":
        addr_text, addr_kb = address_content()
        go_text_screen(chat_id, message_id, addr_text, addr_kb, is_photo)
        waiting_for_address[chat_id] = message_id; return

    if data == "checkout_check_address":
        addr = user_address.get(chat_id, "No address saved.")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Back", callback_data="checkout_addr_back"))
        nav_text(f"📦 Your saved address:\n\n<code>{addr}</code>", kb); return

    if data == "checkout_addr_back":
        conf_text = ("🎉 Your address has been encrypted using the seller's public key and saved. "
                     "The address will be revealed to the seller after the order is paid for.")
        kb = types.InlineKeyboardMarkup()
        kb.row(
            types.InlineKeyboardButton("Check Delivery Address", callback_data="checkout_check_address"),
            types.InlineKeyboardButton("Remove Delivery Address", callback_data="checkout_remove_address")
        )
        kb.add(types.InlineKeyboardButton("Continue checkout", callback_data="checkout_continue"))
        nav_text(conf_text, kb); return

    if data == "checkout_remove_address":
        user_address.pop(chat_id, None)
        addr_text, addr_kb = address_content()
        go_text_screen(chat_id, message_id, addr_text, addr_kb, is_photo)
        waiting_for_address[chat_id] = message_id; return

    if data == "checkout_continue":
        nav_text(*checkout_content(chat_id)); return

    if data == "checkout_delivery": nav_screen(*delivery_content(), "checkout_delivery"); return
    if data.startswith("pay_"):
        user_payment[chat_id] = {"pay_btc": "Bitcoin", "pay_ltc": "Litecoin", "pay_xmr": "Monero"}.get(data, data)
        nav_text(*checkout_content(chat_id)); return
    if data == "del_tracked24":
        user_delivery[chat_id] = "Tracked24 - £5.00"
        nav_text(*checkout_content(chat_id)); return
    if data == "del_special_delivery":
        user_delivery[chat_id] = "Special Delivery Guaranteed by 1PM - £10.00"
        nav_text(*checkout_content(chat_id)); return
    if data == "del_free":
        user_delivery[chat_id] = "FREE POSTAGE"; nav_text(*checkout_content(chat_id)); return
    if data == "checkout_confirm":
        invoice_num = user_pending_invoice.get(chat_id, "---")
        text = (f"⚠️ Confirmation of order creation {invoice_num}\n\n"
                "Before proceeding with the checkout, make sure you are well-informed about how to pay "
                "with cryptocurrency. Ensure that you pay the <b>EXACT</b> crypto amount mentioned and "
                "not the equivalent fiat amount. Be aware of any wallet fees that may be deducted during "
                "the transaction process. It's crucial to follow these steps to ensure that your order "
                "will not been cancelled.")
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Yes, I understand", callback_data="checkout_final_confirm"))
        kb.add(types.InlineKeyboardButton("Back", callback_data="checkout"))
        nav_text(text, kb); return

    if data == "checkout_final_confirm":
        invoice, total, order = save_order(chat_id)
        try:
            user = call.from_user
            uname = f"@{user.username}" if user.username else user.first_name
            notify_admins(
                f"🛒 <b>New Order Initiated</b>\n"
                f"👤 User: {uname} (<code>{chat_id}</code>)\n"
                f"🧾 Invoice: {order['invoice']}\n"
                f"💰 Total: £{order['total']:.2f}\n"
                f"💳 Payment: {order.get('payment_method', 'N/A')}"
            )
        except Exception:
            pass
        nav_screen(*payment_pending_content(order), "order_detail"); return
    if data == "orders": nav_screen(*orders_content(chat_id), "orders"); return

    if data.startswith("wl_open_"):
        prefix = data[len("wl_open_"):]
        if prefix in PRODUCTS:
            nav_push(chat_id, nav_current.get(chat_id))
            nav_current[chat_id] = prefix
            content_fn, default_qty, _, _ = PRODUCTS[prefix]
            set_quantity(chat_id, default_qty)
            open_product(chat_id, message_id, content_fn, is_photo)
        return

    # A Back press: return to the screen actually visited before this one,
    # not the static target the current screen was built with. That target
    # survives as the fallback for when there is no real history yet (e.g.
    # a fresh /start, or a screen opened by a direct link).
    if data.startswith("back:"):
        target = nav_pop(chat_id, data[len("back:"):])
        if target == "main_back":
            waiting_for_contact.discard(chat_id)
            nav_stack.pop(chat_id, None)
            nav_current.pop(chat_id, None)
            nav_screen(*main_menu_content(chat_id), "menu")
            return
        nav_current[chat_id] = target
        if target in SCREEN_IMAGES:
            screen_fn, image_key = SCREEN_IMAGES[target]
            screen_text, screen_kb = screen_fn(chat_id)
            open_screen(chat_id, message_id, screen_text, screen_kb, is_photo, image_key)
        elif target in PRODUCTS:
            content_fn, default_qty, _step, _name = PRODUCTS[target]
            set_quantity(chat_id, default_qty)
            open_product(chat_id, message_id, content_fn, is_photo)
        return

    if data in SCREEN_IMAGES:
        nav_push(chat_id, nav_current.get(chat_id))
        nav_current[chat_id] = data
        screen_fn, image_key = SCREEN_IMAGES[data]
        screen_text, screen_kb = screen_fn(chat_id)
        open_screen(chat_id, message_id, screen_text, screen_kb, is_photo, image_key)
        return

    # Any product opens the same way, so one branch covers the whole registry.
    # This must stay above the suffix handling below, which matches on
    # "<prefix>_increase" and friends rather than the bare prefix.
    if data in PRODUCTS:
        nav_push(chat_id, nav_current.get(chat_id))
        nav_current[chat_id] = data
        content_fn, default_qty, _step, _name = PRODUCTS[data]
        set_quantity(chat_id, default_qty)
        open_product(chat_id, message_id, content_fn, is_photo)
        return

    if data == "about":             nav_screen(*about_content(), "about")
    elif data == "terms":           nav_screen(*terms_content(), "terms")
    elif data == "faq":             nav_screen(*faq_content(), "faq")
    elif data in FAQ_ANSWERS:       nav_screen(*faq_answer_content(data), "faq")
    elif data == "wishlist":        nav_screen(*wishlist_content(chat_id), "profile")
    elif data == "main_back":
        waiting_for_contact.discard(chat_id)
        nav_stack.pop(chat_id, None)
        nav_current.pop(chat_id, None)
        nav_screen(*main_menu_content(chat_id), "menu")
    elif data == "cat1":            nav_text(*dissconnectors_content(chat_id))
    elif data == "cat2":            nav_text(*egg_content(chat_id))
    elif data == "cat4":        nav_text(*peanut_menu_content(chat_id))
    elif data == "cat5":        nav_text(*sugar_content(chat_id))
    elif data == "kettle_menu": nav_text(*kettle_menu_content(chat_id))
    elif data == "2peanut":     nav_text(*brownpeanut_content(chat_id))
    elif data == "mpeanut":     nav_text(*mpeanut_content(chat_id))
    elif data == "cart_main":   nav_screen(*cart_content(chat_id), "cart")
    elif data == "p_offer_stardawg":
        set_quantity(chat_id, 10.0);  open_product(chat_id, message_id, p_offer_stardawg_content, is_photo)
    elif data == "p_offer_gelato41":
        set_quantity(chat_id, 10.0);  open_product(chat_id, message_id, p_offer_gelato41_content, is_photo)
    elif data == "p_offer_bluenerdz":
        set_quantity(chat_id, 10.0);  open_product(chat_id, message_id, p_offer_bluenerdz_content, is_photo)
    elif data == "p_bar_hash":
        set_quantity(chat_id, 1.0);   open_product(chat_id, message_id, p_bar_hash_content, is_photo)
    elif data == "p_icecream":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, p_icecream_content, is_photo)
    elif data == "p_havana":
        set_quantity(chat_id, 1.0);   open_product(chat_id, message_id, p_havana_content, is_photo)
    elif data == "p_gelato_ext":
        set_quantity(chat_id, 1.0);   open_product(chat_id, message_id, p_gelato_ext_content, is_photo)
    elif data == "p_airheadz":
        set_quantity(chat_id, 1.0);   open_product(chat_id, message_id, p_airheadz_content, is_photo)
    elif data == "p_mochi":
        set_quantity(chat_id, 1.0);   open_product(chat_id, message_id, p_mochi_content, is_photo)
    elif data == "p_sour_runtz":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, p_sour_runtz_content, is_photo)
    elif data == "p_fire_runtz":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, p_fire_runtz_content, is_photo)
    elif data == "p_dosi":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, p_dosi_content, is_photo)
    elif data == "p_iceage":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, p_iceage_content, is_photo)
    elif data == "p_twix":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, p_twix_content, is_photo)
    elif data == "p_hash_bundle":
        set_quantity(chat_id, 1.0);   open_product(chat_id, message_id, p_hash_bundle_content, is_photo)
    elif data == "p_mixmatch":
        set_quantity(chat_id, 1.0);   open_product(chat_id, message_id, p_mixmatch_content, is_photo)
    elif data == "p_3uk":
        set_quantity(chat_id, 1.0);   open_product(chat_id, message_id, p_3uk_content, is_photo)
    elif data == "p_survbox":
        set_quantity(chat_id, 1.0);   open_product(chat_id, message_id, p_survbox_content, is_photo)
    elif data == "p_vape1g":
        set_quantity(chat_id, 1.0);   open_product(chat_id, message_id, p_vape1g_content, is_photo)
    elif data == "flower_skywalker":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, flower_skywalker_content, is_photo)
    elif data == "flower_runtz":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, flower_runtz_content, is_photo)
    elif data == "flower_criminal":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, flower_criminal_content, is_photo)
    elif data == "flower_mccake":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, flower_mccake_content, is_photo)
    elif data == "flower_stardawg":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, flower_stardawg_content, is_photo)
    elif data == "flower_cherpaya":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, flower_cherpaya_content, is_photo)
    elif data == "flower_amnesia":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, flower_amnesia_content, is_photo)
    elif data == "flower_cherry":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, flower_cherry_content, is_photo)
    elif data == "flower_gelato":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, flower_gelato_content, is_photo)
    elif data == "flower_cream47":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, flower_cream47_content, is_photo)
    elif data == "flower_godfather":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, flower_godfather_content, is_photo)
    elif data == "flower_frosted_lemon":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, flower_frosted_lemon_content, is_photo)
    elif data == "flower_wedding_cake":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, flower_wedding_cake_content, is_photo)
    elif data == "p_temple":
        set_quantity(chat_id, 5.0);   open_product(chat_id, message_id, p_temple_content, is_photo)
    elif data == "p_ketama":
        set_quantity(chat_id, 5.0);   open_product(chat_id, message_id, p_ketama_content, is_photo)
    elif data == "p_che":
        set_quantity(chat_id, 5.0);   open_product(chat_id, message_id, p_che_content, is_photo)
    elif data == "p_drago":
        set_quantity(chat_id, 5.0);   open_product(chat_id, message_id, p_drago_content, is_photo)
    elif data == "p_donkey":
        set_quantity(chat_id, 5.0);   open_product(chat_id, message_id, p_donkey_content, is_photo)
    elif data == "p_bss40":
        set_quantity(chat_id, 1.0);   open_product(chat_id, message_id, p_bss40_content, is_photo)
    elif data == "p_mim25":
        set_quantity(chat_id, 1.0);   open_product(chat_id, message_id, p_mim25_content, is_photo)
    elif data == "p_stardawg_sm":
        set_quantity(chat_id, 20.0);  open_product(chat_id, message_id, p_stardawg_sm_content, is_photo)
    elif data == "p_shake":
        set_quantity(chat_id, 30.0);  open_product(chat_id, message_id, p_shake_content, is_photo)
    elif data.startswith("announce_open_"):
        prefix = data[len("announce_open_"):]
        if prefix in PRODUCTS:
            content_fn, default_qty, _, _ = PRODUCTS[prefix]
            set_quantity(chat_id, default_qty)
            try: bot.delete_message(chat_id, message_id)
            except: pass
            menu_mid = user_main_menu_msg_id.get(chat_id)
            if not menu_mid:
                # No open menu yet — create one first, then open product inside it
                show_main_with_warning(chat_id)
                menu_mid = user_main_menu_msg_id.get(chat_id)
            if menu_mid:
                open_product(chat_id, menu_mid, content_fn, False)
    elif data == "kettle_10":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, kettle_10_content, is_photo)
    elif data == "kettle_indian":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, indian_kettle_content, is_photo)
    elif data == "kettle_dutch":
        set_quantity(chat_id, 14.0);  open_product(chat_id, message_id, dutch_kettle_content, is_photo)
    elif data == "eggwhite":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, eggwhite_content, is_photo)
    elif data == "egg_black":
        set_quantity(chat_id, 10.0);  open_product(chat_id, message_id, egg_black_content, is_photo)
    elif data == "brownpeanut_g":
        set_quantity(chat_id, 25.0);  open_product(chat_id, message_id, brownpeanut_g_content, is_photo)
    elif data == "brownpeanut_p":
        set_quantity(chat_id, 10.0);  open_product(chat_id, message_id, brownpeanut_p_content, is_photo)
    elif data == "dpeanut":
        set_quantity(chat_id, 1.0);   open_product(chat_id, message_id, dpeanut_content, is_photo)
    elif data == "mpeanut_c":
        set_quantity(chat_id, 2.0);   open_product(chat_id, message_id, mpeanut_c_content, is_photo)
    elif data == "apeanut_s":
        set_quantity(chat_id, 14.0);  open_product(chat_id, message_id, apeanut_s_content, is_photo)
    elif data == "Stilton_menu":
        set_quantity(chat_id, 7.0);   open_product(chat_id, message_id, speed_content, is_photo)
    elif data == "speed_menu":
        set_quantity(chat_id, 7.0);   open_product(chat_id, message_id, speed_content, is_photo)
    elif data == "cok":
        set_quantity(chat_id, 1.75);  open_product(chat_id, message_id, coke_content, is_photo)
    elif data == "mk":
        set_quantity(chat_id, 3.5);   open_product(chat_id, message_id, mkat_content, is_photo)
    else:
        for prefix, (content_fn, default_qty, step, name) in PRODUCTS.items():
            if data == f"{prefix}_decrease":
                set_quantity(chat_id, max(default_qty, round(get_quantity(chat_id) - step, 2)))
                update_product(chat_id, message_id, content_fn, is_photo); return
            elif data == f"{prefix}_increase":
                set_quantity(chat_id, round(get_quantity(chat_id) + step, 2))
                update_product(chat_id, message_id, content_fn, is_photo); return
            elif data == f"{prefix}_custom_qty":
                cancel_kb = types.InlineKeyboardMarkup()
                cancel_kb.add(types.InlineKeyboardButton("Cancel", callback_data=f"{prefix}_cancel_qty"))
                prompt_text = (f"Send the quantity of the product you want to add to the "
                               f"shopping basket in the chat. The minimum quantity is {default_qty:.2f} gr.")
                _, _, image_key = content_fn(chat_id)
                photo_id = get_product_image(image_key, chat_id) if image_key else None
                if photo_id:
                    sent = bot.send_photo(chat_id, photo_id, caption=prompt_text,
                                          reply_markup=cancel_kb)
                else:
                    sent = bot.send_message(chat_id, prompt_text, reply_markup=cancel_kb,
                                            reply_to_message_id=message_id)
                waiting_for_qty[chat_id] = (prefix, sent.message_id, message_id)
                return
            elif data == f"{prefix}_cancel_qty":
                if chat_id in waiting_for_qty:
                    _, prompt_mid, product_mid = waiting_for_qty.pop(chat_id)
                    try: bot.delete_message(chat_id, prompt_mid)
                    except: pass
                return
            elif data == f"{prefix}_add_to_cart":
                qty = get_quantity(chat_id)
                price = PRICE_FNS[prefix](qty)
                unit = PRODUCT_UNITS.get(prefix, "gr")
                add_to_cart(chat_id, name, qty, price, unit)
                bot.answer_callback_query(call.id, f"✅ {name} added to cart", show_alert=True)
                update_product(chat_id, message_id, content_fn, is_photo); return
            elif data == f"{prefix}_wishlist":
                db_toggle_wishlist(chat_id, prefix)
                update_product(chat_id, message_id, content_fn, is_photo); return
            elif data == f"{prefix}_reviews":
                user_review_product[chat_id] = prefix
                # Opening reviews is itself a forward step, so Back can pop
                # straight to the product - paging/filtering within reviews
                # (the rv_ branch below) reuses this same screen and must
                # not push again.
                nav_push(chat_id, nav_current.get(chat_id))
                nav_current[chat_id] = f"__reviews__:{prefix}"
                back_cb = prefix
                text, kb = reviews_content(chat_id, 1, 0, prefix=prefix, back_cb=back_cb)
                nav_screen(text, kb, "reviews"); return
        # PGP key — send as downloadable txt file
        if data == "pgp":
            _send_pgp(chat_id)
            return

        # Price list — send file from files/ subfolder
        if data == "price_list":
            for fname in ("price_list.txt", "price_list.pdf", "price_list.jpg", "price_list.jpeg", "price_list.png"):
                price_list_path = os.path.join(_FILES_DIR, fname)
                if os.path.isfile(price_list_path):
                    with open(price_list_path, "rb") as f:
                        del_kb = types.InlineKeyboardMarkup()
                        del_kb.add(types.InlineKeyboardButton("🗑 Delete Message", callback_data="delete_this_msg"))
                        rid = user_last_msg_id.get(chat_id)
                        if fname.endswith((".txt", ".pdf")):
                            bot.send_document(chat_id, f, caption="An up-to-date list of the vendor's prices has been generated for you", reply_markup=del_kb, reply_to_message_id=rid)
                        else:
                            bot.send_photo(chat_id, f, caption="An up-to-date list of the vendor's prices has been generated for you", reply_markup=del_kb, reply_to_message_id=rid)
                    return
            bot.answer_callback_query(call.id, "Price list not available yet.", show_alert=True)
            return

        # main-menu ratings button
        if data == "reviews_page1":
            user_review_product[chat_id] = None
            text, kb = reviews_content(chat_id, 1, 0, prefix=None, back_cb="main_back")
            nav_screen(text, kb, "reviews"); return
        # rv_{mode}_{stars}_{page}  — reviews pagination / filter
        if data.startswith("rv_"):
            parts = data.split("_")
            # parts: ['rv', mode, stars, page]
            if len(parts) == 4:
                mode  = parts[1]
                stars = int(parts[2])
                page  = int(parts[3])
                if mode == "p":
                    prefix = user_review_product.get(chat_id)
                    back_cb = prefix if prefix else "main_back"
                else:
                    prefix  = None
                    back_cb = "main_back"
                text, kb = reviews_content(chat_id, page, stars, prefix=prefix, back_cb=back_cb)
                nav_screen(text, kb, "reviews"); return
        bot.answer_callback_query(call.id, "Coming soon.", show_alert=True)

# =========================
# INIT AND RUN
# =========================

init_db()
notified_expired.update(db_load_existing_expired())
_verified_rows = db_load_verified_users()
for _cid, _phrase in _verified_rows:
    verified_users.add(_cid)
    if _phrase:
        user_secret_phrase[_cid] = _phrase
for _cid, _mid in db_load_warning_msg_ids():
    if _mid:
        user_warning_msg_id[_cid] = _mid
init_baselines()
threading.Thread(target=order_expiry_watcher, daemon=True).start()
threading.Thread(target=payment_monitor, daemon=True).start()

if USE_PG:
    print("[DB] Connected to PostgreSQL")
else:
    from database import DB_PATH
    print(f"[DB] SQLite → {DB_PATH}")
print(f"[STARTUP] Loaded {len(verified_users)} verified users from DB")
print("Bot running...")

# polling() only guards the update loop itself — the get_me() call it makes on
# the way in is unprotected, so a timeout there would otherwise kill the process.
while True:
    try:
        bot.polling(non_stop=True, timeout=30, long_polling_timeout=30)
        # Nothing calls stop_polling(), so a clean return is unexpected. Pause
        # before restarting so an immediate return cannot spin the CPU.
        logging.warning("Polling returned unexpectedly; restarting.")
    except Exception as e:
        logging.exception("Polling stopped: %s", e)
    time.sleep(5)
