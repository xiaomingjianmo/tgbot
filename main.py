# main.py — Telegram 群杀广机器人（SQLite 持久化 + 关键词 + Gemini AI + 样本采集/导出）
import os, re, time, json, threading, logging, sqlite3, random
from flask import Flask
from telebot import TeleBot
import google.generativeai as genai

# ========= 基础环境 =========
TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not set.")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL   = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
DEFAULT_AI_ON  = os.environ.get("AI_ON", "true").lower() == "true"
DEFAULT_AI_SCORE = float(os.environ.get("AI_SCORE", "0.70"))

WARN_THRESHOLD = int(os.environ.get("WARN_THRESHOLD", "2"))
MUTE_SECONDS   = int(os.environ.get("MUTE_SECONDS", "3600"))  # 0 => kick
DELETE_NOTICE  = os.environ.get("DELETE_NOTICE", "true").lower() == "true"

DB_PATH = os.path.join(os.path.dirname(__file__), "bot.db")

logging.basicConfig(
    filename=os.path.join(os.path.dirname(__file__), "bot.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

bot = TeleBot(TOKEN)
app = Flask(__name__)

# 运行时缓存（仅做加速；真实数据存 SQLite）
chat_regex_cache = {}   # chat_id -> compiled regex
gemini_model = None

# ========= 初始化 Gemini =========
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        gemini_model = genai.GenerativeModel(GEMINI_MODEL)
    except Exception as e:
        logging.error(f"Gemini init failed: {e}")

# ========= SQLite 工具 =========
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS keywords (
            chat_id INTEGER NOT NULL,
            kw TEXT NOT NULL,
            PRIMARY KEY (chat_id, kw)
        );
        CREATE TABLE IF NOT EXISTS warns (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            warns INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS settings (
            chat_id INTEGER PRIMARY KEY,
            ai_on INTEGER NOT NULL,
            ai_score REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ai_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            user_id INTEGER,
            text TEXT,
            is_ad INTEGER,
            score REAL,
            reason TEXT,
            ts INTEGER
        );
        """)
        conn.commit()

def with_retry(fn, retries=3, base_sleep=0.05):
    for i in range(retries):
        try:
            return fn()
        except sqlite3.OperationalError:
            time.sleep(base_sleep * (2 ** i) + random.random() * 0.02)
    return fn()

# ========= DB 读写 =========
def db_get_keywords(chat_id):
    def _q():
        with get_conn() as conn:
            cur = conn.execute("SELECT kw FROM keywords WHERE chat_id=?", (chat_id,))
            return [r[0] for r in cur.fetchall()]
    return with_retry(_q)

def db_add_keywords(chat_id, kws):
    def _q():
        with get_conn() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO keywords(chat_id, kw) VALUES(?,?)",
                [(chat_id, k) for k in kws]
            )
            conn.commit()
    with_retry(_q)

def db_rm_keywords(chat_id, kws):
    def _q():
        with get_conn() as conn:
            conn.executemany(
                "DELETE FROM keywords WHERE chat_id=? AND kw=?",
                [(chat_id, k) for k in kws]
            )
            conn.commit()
    with_retry(_q)

def db_clear_keywords(chat_id):
    def _q():
        with get_conn() as conn:
            conn.execute("DELETE FROM keywords WHERE chat_id=?", (chat_id,))
            conn.commit()
    with_retry(_q)

def db_get_warns(chat_id, user_id):
    def _q():
        with get_conn() as conn:
            cur = conn.execute("SELECT warns FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            row = cur.fetchone()
            return row[0] if row else 0
    return with_retry(_q)

def db_inc_warns(chat_id, user_id):
    def _q():
        with get_conn() as conn:
            cur = conn.execute("SELECT warns FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            row = cur.fetchone()
            if row:
                conn.execute("UPDATE warns SET warns=warns+1 WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            else:
                conn.execute("INSERT INTO warns(chat_id,user_id,warns) VALUES(?,?,1)", (chat_id, user_id))
            conn.commit()
            cur = conn.execute("SELECT warns FROM warns WHERE chat_id=? AND user_id=?", (chat_id, user_id))
            return cur.fetchone()[0]
    return with_retry(_q)

def db_clear_warns(chat_id):
    def _q():
        with get_conn() as conn:
            conn.execute("DELETE FROM warns WHERE chat_id=?", (chat_id,))
            conn.commit()
    with_retry(_q)

def db_get_settings(chat_id):
    def _q():
        with get_conn() as conn:
            cur = conn.execute("SELECT ai_on, ai_score FROM settings WHERE chat_id=?", (chat_id,))
            row = cur.fetchone()
            if row:
                return bool(row[0]), float(row[1])
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO settings(chat_id, ai_on, ai_score) VALUES(?,?,?)",
                    (chat_id, 1 if DEFAULT_AI_ON else 0, DEFAULT_AI_SCORE)
                )
                conn.commit()
                return DEFAULT_AI_ON, DEFAULT_AI_SCORE
    return with_retry(_q)

def db_set_ai_on(chat_id, on: bool):
    def _q():
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings(chat_id, ai_on, ai_score) VALUES(?,?,COALESCE((SELECT ai_score FROM settings WHERE chat_id=?),?))",
                (chat_id, 1 if on else 0, chat_id, DEFAULT_AI_SCORE)
            )
            conn.commit()
    with_retry(_q)

def db_set_ai_score(chat_id, score: float):
    def _q():
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings(chat_id, ai_on, ai_score) VALUES(?,COALESCE((SELECT ai_on FROM settings WHERE chat_id=?),?),?)",
                (chat_id, chat_id, 1 if DEFAULT_AI_ON else 0, score)
            )
            conn.commit()
    with_retry(_q)

def save_ai_sample(chat_id, user_id, text, is_ad, score, reason):
    def _q():
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO ai_samples(chat_id,user_id,text,is_ad,score,reason,ts) VALUES(?,?,?,?,?,?,?)",
                (chat_id, user_id, text[:1000], int(is_ad), float(score), str(reason or ""), int(time.time()))
            )
            conn.commit()
    with_retry(_q)

# ========= 正则缓存 =========
def build_regex(chat_id):
    kws = db_get_keywords(chat_id)
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
    return chat_regex_cache.get(chat_id) or build_regex(chat_id)

# ========= 权限/处罚 =========
def is_admin(chat_id, user_id):
    try:
        m = bot.get_chat_member(chat_id, user_id)
        return m.status in ("administrator", "creator")
    except Exception:
        return False

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

def handle_violation(message, reason="命中关键词/AI"):
    cid, uid = message.chat.id, message.from_user.id
    warns = db_inc_warns(cid, uid)
    try:
        bot.delete_message(cid, message.message_id)
    except Exception:
        pass
    if DELETE_NOTICE:
        bot.send_message(cid, f"🚨 已删除 {message.from_user.first_name} 的消息（{reason}，警告 {warns}）。")
    punish(cid, uid, message.from_user.first_name, warns)

# ========= Gemini 判定（强化 Prompt） =========
AI_PROMPT = """
你是一个 Telegram 群反广告检测 AI。
你的任务是判断一条消息是否为广告、推广、诈骗、引流、兼职、返利等内容。

严格按照以下格式仅返回 JSON：
{"is_ad": true/false, "score": 0.0~1.0, "reason": "简短中文说明"}

## 分类规则：
- 含有返利、秒出、推广、兼职、加群、下单、扫码、高收益、U、本金、代投、日结 等字样的，为广告。
- 含有链接（http、t.me、tg.me、qq、vx、微信号等）并伴随金钱诱导，为广告。
- 含有团队口号（如“汉哥团队”、“星耀团队”、“信誉经营X年”）的，为广告。
- 含有 QQ、微信、TG 联系信息（@xxx、VX:xxx、QQ号等）且意图引导加人，为广告。
- 含有“开上保时捷”“拉人返利”“群发”等营销内容的，为广告。
- 含有拼多多助力、淘宝返现、币圈空投、NFT 推广等，为广告。
- 明显的聊天内容（例如“今天行情怎么样？”、“你吃饭了吗？”、“有谁用过这个机器人？”）为非广告。
- 不确定的情况给出 score 接近 0.5。

## 示例：
用户消息：「加入我们高收益返利群！秒出返利，点击链接：https://t.me/xxxxx ⭐️汉哥团队˙信誉经营十三年保障 @tw_hange 抢了500U，还有，快去！」  
返回：{"is_ad": true, "score": 0.98, "reason": "典型返利推广广告"}

用户消息：「今天的行情分析好像不对？」  
返回：{"is_ad": false, "score": 0.10, "reason": "普通讨论"}

用户消息：「VX:abcd1234 加我领红包」  
返回：{"is_ad": true, "score": 0.90, "reason": "含微信引流"}

用户消息：「群主可以把昨天的链接发一下吗？」  
返回：{"is_ad": false, "score": 0.20, "reason": "正常请求"}

开始判断：
"""

def ai_is_ad(text: str):
    if not gemini_model:
        return (False, 0.0, "ai_offline")
    try:
        payload = f"{AI_PROMPT}\n\nMessage:\n{text.strip()[:4000]}"
        resp = gemini_model.generate_content(payload)
        raw = getattr(resp, "text", "") or ""
        start = raw.find("{"); end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            j = json.loads(raw[start:end+1])
            return bool(j.get("is_ad", False)), float(j.get("score", 0.0)), str(j.get("reason", ""))
        return (False, 0.0, "parse_fail")
    except Exception as e:
        logging.warning(f"Gemini error: {e}")
        return (False, 0.0, "ai_error")

# ========= 管理命令 =========
def admin_only(func):
    def wrapper(message):
        try:
            if not is_admin(message.chat.id, message.from_user.id):
                bot.reply_to(message, "只有群管理员可以使用该命令。")
                return
        except Exception:
            return
        return func(message)
    return wrapper

@bot.message_handler(commands=["addkw"])
@admin_only
def cmd_addkw(msg):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "用法：/addkw 关键词1 关键词2 ...；正则用 /.../ 包裹。")
        return
    items = [s for s in parts[1].split() if s]
    db_add_keywords(msg.chat.id, items)
    build_regex(msg.chat.id)
    bot.reply_to(msg, f"✅ 已添加 {len(items)} 个关键词。")

@bot.message_handler(commands=["rmkw"])
@admin_only
def cmd_rmkw(msg):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "用法：/rmkw 关键词1 关键词2 ...（与添加时一致，包括正则外壳）")
        return
    items = [s for s in parts[1].split() if s]
    db_rm_keywords(msg.chat.id, items)
    build_regex(msg.chat.id)
    bot.reply_to(msg, f"🧹 已移除 {len(items)} 个关键词。")

@bot.message_handler(commands=["listkw"])
@admin_only
def cmd_listkw(msg):
    kws = db_get_keywords(msg.chat.id)
    if not kws:
        bot.reply_to(msg, "当前无关键词。用 /addkw 添加。")
    else:
        preview = "\n".join(sorted(kws)[:120])
        more = "" if len(kws) <= 120 else f"\n……共 {len(kws)} 个"
        bot.reply_to(msg, f"当前关键词（最多显示 120 条）：\n{preview}{more}\n以 /.../ 包裹的是正则。")

@bot.message_handler(commands=["clearkw"])
@admin_only
def cmd_clearkw(msg):
    db_clear_keywords(msg.chat.id)
    build_regex(msg.chat.id)
    bot.reply_to(msg, "🧽 已清空关键词。")

@bot.message_handler(commands=["warns", "resetwarns"])
@admin_only
def cmd_warns(msg):
    cid = msg.chat.id
    if msg.text.startswith("/resetwarns"):
        db_clear_warns(cid)
        bot.reply_to(msg, "已清空本群警告计数。")
    else:
        bot.reply_to(msg, "已清空命令可用：/resetwarns\n（详细名单可后续加导出功能）")

@bot.message_handler(commands=["aion", "aioff"])
@admin_only
def cmd_ai_switch(msg):
    on = msg.text.startswith("/aion")
    db_set_ai_on(msg.chat.id, on)
    cur_on, cur_score = db_get_settings(msg.chat.id)
    bot.reply_to(msg, f"🤖 AI 识别已{'开启' if cur_on else '关闭'}（阈值 {cur_score:.2f}）。")

@bot.message_handler(commands=["aiscore"])
@admin_only
def cmd_ai_score(msg):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        on, sc = db_get_settings(msg.chat.id)
        bot.reply_to(msg, f"用法：/aiscore 0.70   （当前 {sc:.2f}，AI {'开' if on else '关'}）")
        return
    try:
        val = float(parts[1])
        if not (0.0 <= val <= 1.0):
            raise ValueError
        db_set_ai_score(msg.chat.id, val)
        on, sc = db_get_settings(msg.chat.id)
        bot.reply_to(msg, f"✅ AI 阈值已设为 {sc:.2f}（越高越保守）。")
    except Exception:
        bot.reply_to(msg, "请输入 0~1 之间的小数，如 0.70")

@bot.message_handler(commands=["exportkw"])
@admin_only
def cmd_exportkw(msg):
    kws = db_get_keywords(msg.chat.id)
    data = {"chat_id": msg.chat.id, "keywords": sorted(kws)}
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if len(text) <= 3900:
        bot.reply_to(msg, "当前关键词导出：\n```\n" + text + "\n```", parse_mode="Markdown")
    else:
        tmp = os.path.join(os.path.dirname(__file__), f"kw_{msg.chat.id}.json")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        with open(tmp, "rb") as f:
            bot.send_document(msg.chat.id, f, caption="关键词导出 JSON")
        try: os.remove(tmp)
        except: pass

@bot.message_handler(commands=["importkw"])
@admin_only
def cmd_importkw(msg):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "用法：/importkw {json}\njson 结构：{\"chat_id\":<可忽略>, \"keywords\":[\"词1\",\"/正则/\",...]}")
        return
    try:
        j = json.loads(parts[1])
        kws = j.get("keywords", [])
        if not isinstance(kws, list):
            raise ValueError
        db_clear_keywords(msg.chat.id)
        if kws:
            db_add_keywords(msg.chat.id, kws)
        build_regex(msg.chat.id)
        bot.reply_to(msg, f"✅ 已导入 {len(kws)} 个关键词。")
    except Exception as e:
        bot.reply_to(msg, f"导入失败：{e}")

@bot.message_handler(commands=["aiexport"])
@admin_only
def cmd_aiexport(msg):
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT text,is_ad,score,reason FROM ai_samples WHERE chat_id=? ORDER BY ts DESC LIMIT 300",
            (msg.chat.id,)
        )
        rows = cur.fetchall()
    if not rows:
        bot.reply_to(msg, "暂无 AI 样本。")
        return
    data = [{"text": t, "is_ad": bool(i), "score": float(s), "reason": r} for (t,i,s,r) in rows]
    path = os.path.join(os.path.dirname(__file__), f"ai_samples_{msg.chat.id}_{int(time.time())}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    with open(path, "rb") as f:
        bot.send_document(msg.chat.id, f, caption=f"导出 {len(data)} 条 AI 样本（最近）")
    try: os.remove(path)
    except: pass

# ========= 消息过滤 =========
@bot.message_handler(func=lambda m: True, content_types=["text","photo","video","sticker","document","audio","voice"])
def all_messages(m):
    # 放行管理员
    try:
        if is_admin(m.chat.id, m.from_user.id):
            return
    except Exception:
        return

    txt = (m.text or m.caption or "").strip()
    if not txt:
        return

    # 关键词先挡
    rx = ensure_regex(m.chat.id)
    if rx and rx.search(txt):
        handle_violation(m, reason="命中关键词")
        return

    # AI 判定（按群配置）
    ai_on, ai_score = db_get_settings(m.chat.id)
    if ai_on and gemini_model:
        is_ad, score, reason = ai_is_ad(txt)
        # 记录样本（仅本地存储，不外发）
        try:
            save_ai_sample(m.chat.id, m.from_user.id, txt, is_ad, score, reason)
        except Exception as e:
            logging.warning(f"save_ai_sample failed: {e}")
        if is_ad and score >= ai_score:
            handle_violation(m, reason=f"AI判定广告(score={score:.2f})")
            return
    # 放行
    return

# ========= 健康检查 + 启动 =========
@app.route("/")
def index():
    return "OK - AntiSpam bot running (SQLite+AI+Samples).", 200

@app.route("/ping")
def ping():
    return "pong", 200

def start_polling():
    bot.infinity_polling(timeout=60, long_polling_timeout=60)

if __name__ == "__main__":
    init_db()
    threading.Thread(target=start_polling, daemon=True).start()
    port = int(os.environ.get("PORT", "10000"))
    print("Bot is running! Flask health server listening on port", port, flush=True)
    app.run(host="0.0.0.0", port=port)
