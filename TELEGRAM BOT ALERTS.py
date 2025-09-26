import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from collections import defaultdict, deque
import uuid
import threading
import time
import MetaTrader5 as mt5

# === CONFIG ===
BOT_TOKEN = "8294376635:AAEDMnSk9-4v7xD8LsWy13HAUj9Gcxi-dTs"
ALLOWED_USERS = ["8359294930", "5546410468","773213882"]

bot = telebot.TeleBot(BOT_TOKEN)

# === STORAGE ===
price_alerts = defaultdict(list)      # {user_id_str: [(symbol, target_price, type), ...]}
sharpturn_alerts = defaultdict(list)

# symbol -> list of (user_id_str, target, type) for fast checking
symbol_alerts = defaultdict(list)

# Pending flows per user: each flow is a dict with an id, kind and state + data
pending_flows = defaultdict(deque)

# Locks for thread-safe modifications
alerts_lock = threading.Lock()
pending_lock = threading.Lock()

# ---- MT5 init (unchanged) ----
mt5_connected = False
try:
    mt5_connected = mt5.initialize()
    if not mt5_connected:
        print("MT5 initialize() returned False. Make sure MT5 terminal is running and Python integration enabled.")
    else:
        print("MT5 initialized successfully.")
except Exception as e:
    print("MT5 initialization error:", e)
    mt5_connected = False

# ---- Helpers ----
def new_flow(kind):
    return {
        "id": uuid.uuid4().hex[:8],
        "kind": kind,
        "state": "await_symbol",
        # symbol, type, timeframe, price_a, price_b will be filled in as flow proceeds
    }

def find_flow_index_and_flow_locked(user_id_str, flow_id):
    """
    Must be called while holding pending_lock.
    Return (index, flow) or (None, None)
    """
    dq = pending_flows[user_id_str]
    for i, f in enumerate(dq):
        if f["id"] == flow_id:
            return i, f
    return None, None

def ensure_symbol_in_mt5(symbol):
    try:
        info = mt5.symbol_info(symbol)
        if info is not None:
            return True
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
        return info is not None
    except Exception:
        return False

def get_tick_mt5(symbol):
    try:
        return mt5.symbol_info_tick(symbol)
    except Exception:
        return None

# === BOT UI ===
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id_str = str(message.chat.id)
    if user_id_str not in ALLOWED_USERS:
        return bot.reply_to(message, "❌ You are not authorized to use this bot.")
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("📈 Set Price Alert", callback_data="menu_price"),
        InlineKeyboardButton("⚡ SharpTurn Alert", callback_data="menu_sharpturn")
    )
    bot.send_message(message.chat.id, "Hello! Choose an option:", reply_markup=markup)

# === CALLBACK: start flows ===
@bot.callback_query_handler(func=lambda call: call.data == "menu_price")
def menu_price_alert(call):
    user_id_str = str(call.message.chat.id)
    if user_id_str not in ALLOWED_USERS:
        return
    flow = new_flow("price")
    flow["state"] = "await_symbol"
    with pending_lock:
        pending_flows[user_id_str].append(flow)
    bot.send_message(call.message.chat.id,
                     f"✍️ (Price Alert) Enter symbol (case insensitive). This alert id: {flow['id']}")

@bot.callback_query_handler(func=lambda call: call.data == "menu_sharpturn")
def menu_sharpturn_alert(call):
    user_id_str = str(call.message.chat.id)
    if user_id_str not in ALLOWED_USERS:
        return
    flow = new_flow("sharp")
    flow["state"] = "await_symbol"
    with pending_lock:
        pending_flows[user_id_str].append(flow)
    bot.send_message(call.message.chat.id,
                     f"✍️ (SharpTurn) Enter symbol (case insensitive). This alert id: {flow['id']}")

# === CALLBACK: price type selected for price flow ===
# callback_data format: price_type|<flow_id>|<BUY|SELL>
@bot.callback_query_handler(func=lambda call: call.data.startswith("price_type|"))
def price_type_selected(call):
    user_id_str = str(call.message.chat.id)
    if user_id_str not in ALLOWED_USERS:
        return

    parts = call.data.split("|", 2)
    if len(parts) != 3:
        return
    _, flow_id, ptype = parts

    flow_to_notify = None
    with pending_lock:
        idx, flow = find_flow_index_and_flow_locked(user_id_str, flow_id)
        if flow is None:
            flow_to_notify = ("not_waiting", None)
        else:
            if flow["kind"] != "price" or flow["state"] != "await_type":
                flow = None
        if flow is None:
            flow_to_notify = ("not_waiting", None)
        else:
            flow["type"] = ptype
            flow["state"] = "await_price"
            flow_to_notify = ("ok", (flow["symbol"], ptype))

    if flow_to_notify[0] == "not_waiting":
        bot.answer_callback_query(call.id, "Flow not found or not waiting for type.")
    else:
        symbol, ptype = flow_to_notify[1]
        try:
            bot.edit_message_text(f"✅ Type set: {ptype}\n✍️ Now enter target price for {symbol}:",
                                  call.message.chat.id, call.message.message_id)
        except Exception:
            bot.send_message(call.message.chat.id, f"✅ Type set: {ptype}\n✍️ Now enter target price for {symbol}:")
        bot.answer_callback_query(call.id)

# === CALLBACK: sharp timeframe selected ===
@bot.callback_query_handler(func=lambda call: call.data.startswith("sharp_tf|"))
def sharpturn_timeframe_selected(call):
    user_id_str = str(call.message.chat.id)
    if user_id_str not in ALLOWED_USERS:
        return
    parts = call.data.split("|", 2)
    if len(parts) != 3:
        return
    _, flow_id, tf = parts

    flow_to_notify = None
    with pending_lock:
        idx, flow = find_flow_index_and_flow_locked(user_id_str, flow_id)
        if flow is None or flow["kind"] != "sharp" or flow["state"] != "await_timeframe":
            flow_to_notify = ("not_waiting", None)
        else:
            flow["timeframe"] = tf
            flow["state"] = "await_price_a"
            flow_to_notify = ("ok", (flow["symbol"], tf))

    if flow_to_notify[0] == "not_waiting":
        bot.answer_callback_query(call.id, "Flow not found or expired.")
    else:
        symbol, tf = flow_to_notify[1]
        try:
            bot.edit_message_text(f"✅ Timeframe set: {tf}\n✍️ Now enter first price (A) for {symbol}:",
                                  call.message.chat.id, call.message.message_id)
        except Exception:
            bot.send_message(call.message.chat.id, f"✅ Timeframe set: {tf}\n✍️ Now enter first price (A) for {symbol}:")
        bot.answer_callback_query(call.id)

# === MESSAGE HANDLER (flows) ===
@bot.message_handler(func=lambda msg: True)
def handle_messages(message):
    user_id_str = str(message.chat.id)
    if user_id_str not in ALLOWED_USERS:
        return bot.reply_to(message, "❌ You are not authorized to use this bot.")
    text = message.text.strip()

    reply_actions = []

    with pending_lock:
        if pending_flows[user_id_str]:
            flow = pending_flows[user_id_str][0]
            kind = flow.get("kind")
            state = flow.get("state")

            # Price: symbol -> show BUY/SELL
            if kind == "price" and state == "await_symbol":
                flow["symbol"] = text.upper()  # <-- convert to uppercase
                flow["state"] = "await_type"
                cb_buy = f"price_type|{flow['id']}|BUY"
                cb_sell = f"price_type|{flow['id']}|SELL"
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("BUY (>= target)", callback_data=cb_buy))
                markup.add(InlineKeyboardButton("SELL (<= target)", callback_data=cb_sell))
                reply_actions.append(("send_markup", message.chat.id,
                                      f"✅ Symbol set for Price Alert: {flow['symbol']}\nChoose type:", markup))
            # Price: target price
            elif kind == "price" and state == "await_price":
                try:
                    price = float(text)
                except ValueError:
                    reply_actions.append(("send", message.chat.id, "⚠️ Please enter a valid number for the price.", None))
                else:
                    symbol = flow.get("symbol")
                    ptype = flow.get("type", "BUY")
                    flow["state"] = "saving"
                    saved_flow = {"symbol": symbol, "price": price, "ptype": ptype, "user_id_str": user_id_str, "flow_id": flow["id"]}
                    reply_actions.append(("save_price_flow", saved_flow))
            # Sharp: symbol -> show timeframe buttons
            elif kind == "sharp" and state == "await_symbol":
                flow["symbol"] = text.upper()  # <-- convert to uppercase
                flow["state"] = "await_timeframe"
                markup = InlineKeyboardMarkup()
                for tf in ["1m", "5m", "15m", "1h", "4h", "1d", "1w", "1M"]:
                    cb = f"sharp_tf|{flow['id']}|{tf}"
                    markup.add(InlineKeyboardButton(tf, callback_data=cb))
                reply_actions.append(("send_markup", message.chat.id,
                                      f"✅ Symbol set for SharpTurn: {flow['symbol']}\n⏱ Select timeframe:", markup))
            elif kind == "sharp" and state == "await_price_a":
                try:
                    price_a = float(text)
                except ValueError:
                    reply_actions.append(("send", message.chat.id, "⚠️ Please enter a valid number for price A.", None))
                else:
                    flow["price_a"] = price_a
                    flow["state"] = "await_price_b"
                    reply_actions.append(("send", message.chat.id, f"✍️ Now enter second price (B) for {flow['symbol']} on {flow['timeframe']}:", None))
            elif kind == "sharp" and state == "await_price_b":
                try:
                    price_b = float(text)
                except ValueError:
                    reply_actions.append(("send", message.chat.id, "⚠️ Please enter a valid number for price B.", None))
                else:
                    price_a = flow.get("price_a")
                    symbol = flow.get("symbol")
                    tf = flow.get("timeframe")
                    flow["state"] = "saving_sharp"
                    saved_sharp = {"symbol": symbol, "tf": tf, "a": price_a, "b": price_b, "user_id_str": user_id_str, "flow_id": flow["id"]}
                    reply_actions.append(("save_sharp_flow", saved_sharp))

    # execute reply actions outside lock
    for action in reply_actions:
        typ = action[0]
        if typ == "send":
            _, chat_id, text_to_send, _ = action
            bot.send_message(chat_id, text_to_send)
        elif typ == "send_markup":
            _, chat_id, text_to_send, markup = action
            bot.send_message(chat_id, text_to_send, reply_markup=markup)
        elif typ == "save_price_flow":
            saved = action[1]
            user_id_str = saved["user_id_str"]
            symbol = saved["symbol"]
            price = saved["price"]
            ptype = saved["ptype"]
            flow_id = saved["flow_id"]

            if mt5_connected:
                exists = ensure_symbol_in_mt5(symbol)
                if not exists:
                    bot.send_message(int(user_id_str), f"⚠️ Symbol '{symbol}' not found in MT5. Alert not saved.")
                    with pending_lock:
                        idx, f = find_flow_index_and_flow_locked(user_id_str, flow_id)
                        if idx is not None:
                            pending_flows[user_id_str].popleft() if idx == 0 else pending_flows[user_id_str].remove(f)
                    continue
            else:
                bot.send_message(int(user_id_str), "⚠️ Warning: MT5 not connected. Alert saved but will not trigger until MT5 connects.")

            with alerts_lock:
                price_alerts[user_id_str].append((symbol, price, ptype))
                symbol_alerts[symbol].append((user_id_str, price, ptype))

            with pending_lock:
                idx, f = find_flow_index_and_flow_locked(user_id_str, flow_id)
                if idx is not None:
                    if idx == 0:
                        pending_flows[user_id_str].popleft()
                    else:
                        pending_flows[user_id_str].remove(f)

            bot.send_message(int(user_id_str), f"✅ Price alert saved: {symbol} → {price} ({ptype})")

        elif typ == "save_sharp_flow":
            saved = action[1]
            user_id_str = saved["user_id_str"]
            symbol = saved["symbol"]
            tf = saved["tf"]
            a = saved["a"]
            b = saved["b"]
            flow_id = saved["flow_id"]

            with alerts_lock:
                sharpturn_alerts[user_id_str].append((symbol, tf, a, b))

            with pending_lock:
                idx, f = find_flow_index_and_flow_locked(user_id_str, flow_id)
                if idx is not None:
                    if idx == 0:
                        pending_flows[user_id_str].popleft()
                    else:
                        pending_flows[user_id_str].remove(f)

            bot.send_message(int(user_id_str), f"✅ SharpTurn alert saved: {symbol} on {tf} with A={a}, B={b}")

    if not reply_actions:
        if text.lower() == "hi":
            bot.reply_to(message, "hi 👋")

# === listalerts (unchanged) ===
@bot.message_handler(commands=['listalerts'])
def list_alerts(message):
    user_id_str = str(message.chat.id)
    if user_id_str not in ALLOWED_USERS:
        return bot.reply_to(message, "❌ You are not authorized to use this bot.")
    lines = []
    with alerts_lock:
        pa = price_alerts.get(user_id_str, [])
        sa = sharpturn_alerts.get(user_id_str, [])
    if not pa and not sa:
        return bot.reply_to(message, "You have no saved alerts.")
    if pa:
        lines.append("📈 Price Alerts:")
        for s, p, t in pa:
            lines.append(f"  • {s} → {p} ({t})")
    if sa:
        lines.append("\n⚡ SharpTurn Alerts:")
        for s, tf, a, b in sa:
            lines.append(f"  • {s} | {tf} | A={a} B={b}")
    bot.reply_to(message, "\n".join(lines))

# === FAST BACKGROUND: checker using per-symbol ticks (unchanged) ===
def price_alerts_checker(poll_interval=0.2):
    global mt5_connected
    last_tick_time = {}
    while True:
        if not mt5_connected:
            try:
                mt5_connected = mt5.initialize()
                if mt5_connected:
                    print("MT5 re-connected.")
            except Exception:
                mt5_connected = False

        with alerts_lock:
            symbols = list(symbol_alerts.keys())

        for symbol in symbols:
            if not mt5_connected:
                continue
            tick = get_tick_mt5(symbol)
            if not tick:
                continue
            t_msc = getattr(tick, "time_msc", None)
            if t_msc is None:
                t_sec = getattr(tick, "time", None)
                if t_sec is None:
                    continue
                t_msc = int(t_sec * 1000)
            if last_tick_time.get(symbol) == t_msc:
                continue
            last_tick_time[symbol] = t_msc

            cur_price = getattr(tick, "last", None)
            if not cur_price or cur_price <= 0:
                bid = getattr(tick, "bid", None)
                ask = getattr(tick, "ask", None)
                if bid is not None and ask is not None:
                    cur_price = (bid + ask) / 2.0
            try:
                cur_price = float(cur_price)
            except Exception:
                continue

            with alerts_lock:
                alerts_for_symbol = list(symbol_alerts.get(symbol, []))

            for (uid_str, target, typ) in alerts_for_symbol:
                triggered = False
                if typ == "BUY" and cur_price >= target:
                    triggered = True
                if typ == "SELL" and cur_price <= target:
                    triggered = True
                if triggered:
                    try:
                        bot.send_message(int(uid_str), f"🚨 {symbol} {typ} alert: current {cur_price} target {target}")
                    except Exception:
                        pass
                    with alerts_lock:
                        if (uid_str, target, typ) in symbol_alerts.get(symbol, []):
                            symbol_alerts[symbol].remove((uid_str, target, typ))
                            if not symbol_alerts[symbol]:
                                del symbol_alerts[symbol]
                        if (symbol, target, typ) in price_alerts.get(uid_str, []):
                            price_alerts[uid_str].remove((symbol, target, typ))

        time.sleep(poll_interval)

checker_thread = threading.Thread(target=price_alerts_checker, args=(0.2,), daemon=True)
checker_thread.start()

# === MAIN LOOP ===
print("🤖 Bot is running...")
bot.infinity_polling()
