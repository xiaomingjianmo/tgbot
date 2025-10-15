# main.py — Telegram group anti-spam bot (keywords version) for Render Web Service
import os, re, time, threading
from collections import defaultdict
from flask import Flask
from telebot import TeleBot

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is not set. Set it in Render > Environment.")

# --- Bot setup
bot = TeleBot(TOKEN)
app = Flask(__name__)

# --- Config (edit as you like)
WARN_THRESHOLD = int(os.environ.get("WARN_THRESHOLD", "2"))     # warnings before punishment
MUTE_SECONDS   = int(os.environ.get("MUTE_SECONDS", "3600"))    # mute seconds (0 => kick)
DELETE_NOTICE  = os.environ.get("DELETE_NOTICE", "true").lower() == "true"

# per-chat keyword set + compiled regex cache + warnings
chat_keywords   = defaultdict(set)       # chat_id -> {kw,...}; kw string or /regex/
chat_regex_cache= {}                     # chat_id -> compiled regex
user_warnings   = defaultdict(int)       # f"{chat_id}:{user_id}" -> count

def is_admin(chat_id, user_id):
    try:
        m = bot.get_chat_member(chat_id, user_id)
        return m.status in ("administrator", "creator")
    except Exception:
        return False

def build_regex(chat_id):
    kws = chat_keywords.get(chat_id, set())
    if not kws:
        chat_regex_cache.pop(chat_id, None)
        return None
    parts = []
    for kw in kws:
        kw = kw.strip()
        if not kw:
            continue
        if kw.startswith("/") and kw.endswith("/"):
            parts.append(f"(?:{kw[1:-1]})")
        else:
            parts.append(re.escape(kw))
    try:
        rx = re.compile("|".join(parts), re.I | re.DOTALL)
    except re.error:
        rx = re.compile("|".join(re.escape(k) for k in kws), re.I | re.DOTALL)
    chat_regex_cache[chat_id] = rx
    return rx

def ensure_regex(chat_id):
    rx = chat_regex_cache.get(chat_id)
    if rx is None:
        rx = build_regex(chat_id)
    return rx

def punish(chat_id, user_id, name, warns):
    try:
        if warns > WARN_THRESHOLD:
            if MUTE_SECONDS > 0:
                until = int(time.time()) + MUTE_SECONDS
                bot.restrict_chat_member(chat_id, user_id, can_send_messages=False, until_date=until)
                if DELETE_NOTICE:
                    bot.send_message(chat_id, f"🚫 已对 {name} 禁言 {MUTE_SECONDS} 秒（警告 {warns}）。")
            else:
                bot.kick_chat_member(chat_id, user_id)
                if DELETE_NOTICE:
                    bot.send_message(chat_id, f"🛑 已将 {name} 踢出（警告 {warns}）。")
    except Exception as e:
        if DELETE_NOTICE:
            bot.send_message(chat_id, f"⚠️ 处罚失败：{e}")

def handle_violation(message):
    cid, uid = message.chat.id, message.from_user.id
    key = f"{cid}:{uid}"
    user_warnings[key] += 1
    warns = user_warnings[key]
    try:
        bot.delete_message(cid, message.message_id)
    except Exception:
        pass
    if DELETE_NOTICE:
        bot.send_message(cid, f"🚨 已删除 {message.from_user.first_name} 的消息（警告 {warns}）。")
    punish(cid, uid, message.from_user.first_name, warns)

# --- Admin commands
def admin_only(func):
    def wrapper(message):
        if not is_admin(message.chat.id, message.from_user.id):
            bot.reply_to(message, "只有群管理员可以使用该命令。")
            return
        return func(message)
    return wrapper

@bot.message_handler(commands=["addkw"])
@admin_only
def addkw(msg):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "用法：/addkw 关键词1 关键词2 ...\n正则：/addkw /\\b免费(领取|送)/ /https?:\\/\\/t\\.cn/")
        return
    items = [s for s in parts[1].split() if s]
    for k in items:
        chat_keywords[msg.chat.id].add(k)
    build_regex(msg.chat.id)
    bot.reply_to(msg, f"✅ 已添加 {len(items)} 个关键词。")

@bot.message_handler(commands=["rmkw"])
@admin_only
def rmkw(msg):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "用法：/rmkw 关键词1 关键词2 ...（与添加一致，包括正则外壳）")
        return
    items = [s for s in parts[1].split() if s]
    removed = 0
    for k in items:
        if k in chat_keywords[msg.chat.id]:
            chat_keywords[msg.chat.id].remove(k)
            removed += 1
    build_regex(msg.chat.id)
    bot.reply_to(msg, f"🧹 已移除 {removed} 个关键词。")

@bot.message_handler(commands=["listkw"])
@admin_only
def listkw(msg):
    kws = sorted(chat_keywords.get(msg.chat.id, set()))
    if not kws:
        bot.reply_to(msg, "当前无关键词。用 /addkw 添加。")
    else:
        preview = "\n".join(kws[:50])
        more = "" if len(kws) <= 50 else f"\n……共 {len(kws)} 个"
        bot.reply_to(msg, f"当前关键词（前 50 条）：\n{preview}{more}\n说明：以 /.../ 包裹的是正则表达式。")

@bot.message_handler(commands=["clearkw"])
@admin_only
def clearkw(msg):
    chat_keywords[msg.chat.id].clear()
    build_regex(msg.chat.id)
    bot.reply_to(msg, "🧽 已清空关键词。")

@bot.message_handler(commands=["warns", "resetwarns"])
@admin_only
def warns_cmd(msg):
    cid = msg.chat.id
    if msg.text.startswith("/resetwarns"):
        keys = [k for k in list(user_warnings.keys()) if k.startswith(f"{cid}:")]
        for k in keys: user_warnings.pop(k, None)
        bot.reply_to(msg, "已清空本群警告计数。")
    else:
        lines = []
        for k,v in user_warnings.items():
            scid, uid = k.split(":")
            if int(scid) == cid:
                lines.append(f"user_id {uid} -> warns {v}")
        bot.reply_to(msg, "当前警告：\n" + ("\n".join(lines) if lines else "无"))

# --- Message filter
@bot.message_handler(func=lambda m: True, content_types=["text","photo","video","sticker","document","audio","voice"])
def all_messages(m):
    if is_admin(m.chat.id, m.from_user.id):
        return
    txt = m.text or m.caption or ""
    if not txt:
        return
    rx = ensure_regex(m.chat.id)
    if rx and rx.search(txt):
        handle_violation(m)

# --- Flask tiny web server for health check
@app.route("/")
def index():
    return "OK - Telegram AntiSpam Bot running", 200

@app.route("/ping")
def ping():
    return "pong", 200

def start_polling():
    # Start TeleBot long polling in a background thread
    bot.infinity_polling(timeout=60, long_polling_timeout=60)

if __name__ == "__main__":
    # Start bot in background thread
    threading.Thread(target=start_polling, daemon=True).start()
    port = int(os.environ.get("PORT", "10000"))
    print("Bot is running! Flask health server listening on port", port, flush=True)
    app.run(host="0.0.0.0", port=port)