import asyncio
import datetime
import json
import logging
import math
import os
import random
import tempfile
import threading

os.environ.setdefault('MPLBACKEND', 'Agg')

import pytz
import yfinance as yf
from flask import Flask
from gtts import gTTS
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      ReplyKeyboardMarkup, Update, WebAppInfo)
from telegram.error import BadRequest
from telegram.ext import (Application, ApplicationHandlerStop, CallbackQueryHandler,
                          CommandHandler, ContextTypes, ConversationHandler,
                          MessageHandler, TypeHandler, filters)

from texts import next_text

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('bot')
NL = chr(10)

# ---------------- SETTINGS (Render Environment theke ashe) ----------------
BOT_TOKEN = os.environ.get('BOT_TOKEN', '').strip()
_owner = os.environ.get('OWNER_ID', '').strip()
OWNER_ID = int(_owner) if _owner.isdigit() else None
PASSWORDS = [p.strip() for p in (os.environ.get('PASS_ONE', ''), os.environ.get('PASS_TWO', ''),
                                 os.environ.get('PASS_THREE', '')) if p.strip()]
TIMEZONE = pytz.timezone('Asia/Kolkata')
DATA_FILE = 'trades.json'
MUSIC_FILE = 'music.json'
MAX_TRADES = 5
SESSION_LIMIT_MIN = 60
TRADE_SECONDS = 60

SESSIONS = {}
PLANS = {}
SETTINGS = {}
ALERT_CHATS = set()

# ---------------- FLASK (24/7 ping er jonno) ----------------
web = Flask('keepalive')


@web.route('/')
def home():
    return 'Quotex Risk Management Bot is alive!'


def run_web():
    port = int(os.environ.get('PORT', 8080))
    web.run(host='0.0.0.0', port=port)


def keep_alive():
    threading.Thread(target=run_web, daemon=True).start()


# ---------------- DATA ----------------
def now_ist():
    return datetime.datetime.now(TIMEZONE)


def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def day_record(data, day):
    rec = data.get(day)
    if not isinstance(rec, dict):
        rec = {}
        data[day] = rec
    rec.setdefault('wins', 0)
    rec.setdefault('losses', 0)
    rec.setdefault('draws', 0)
    rec.setdefault('profit', 0.0)
    rec.setdefault('history', [])
    return rec


def update_trade_data(result, pnl, extra):
    data = load_json(DATA_FILE)
    today = now_ist().strftime('%Y-%m-%d')
    rec = day_record(data, today)
    if result == 'WIN':
        rec['wins'] += 1
    elif result == 'LOSS':
        rec['losses'] += 1
    else:
        rec['draws'] += 1
    rec['profit'] = round(rec['profit'] + pnl, 2)
    item = {'time': now_ist().strftime('%H:%M:%S'), 'result': result, 'pnl': round(pnl, 2)}
    item.update(extra)
    rec['history'].append(item)
    save_json(DATA_FILE, data)


def stats_text():
    data = load_json(DATA_FILE)
    rec = day_record(data, now_ist().strftime('%Y-%m-%d'))
    total = rec['wins'] + rec['losses']
    wr = (rec['wins'] / total * 100) if total else 0
    week_net = 0.0
    week_w = 0
    week_l = 0
    for i in range(7):
        d = (now_ist() - datetime.timedelta(days=i)).strftime('%Y-%m-%d')
        r = data.get(d)
        if isinstance(r, dict):
            week_net += r.get('profit', 0.0)
            week_w += r.get('wins', 0)
            week_l += r.get('losses', 0)
    return NL.join([
        '📈 *Profit/Loss Stats*',
        '',
        '*আজ:*',
        '🟢 Win: ' + str(rec['wins']) + '   🔴 Loss: ' + str(rec['losses']),
        '🎯 Win Rate: ' + format(wr, '.0f') + '%',
        '💵 Net: $' + format(rec['profit'], '.2f'),
        '',
        '*শেষ ৭ দিন:*',
        '🟢 ' + str(week_w) + ' Win | 🔴 ' + str(week_l) + ' Loss | 💵 Net $' + format(week_net, '.2f'),
    ])


# ---------------- MARKET ----------------
PAIRS = ['EUR/USD', 'GBP/USD', 'AUD/USD', 'NZD/USD', 'USD/JPY', 'USD/CAD', 'USD/CHF', 'EUR/GBP',
         'EUR/JPY', 'GBP/JPY', 'AUD/JPY', 'NZD/JPY', 'CAD/JPY', 'CHF/JPY', 'EUR/AUD', 'EUR/CAD',
         'EUR/CHF', 'GBP/AUD', 'GBP/CAD', 'GBP/CHF', 'AUD/CAD', 'AUD/NZD']
SCAN_PAIRS = ['EUR/USD', 'GBP/USD', 'USD/JPY', 'AUD/USD', 'USD/CAD', 'EUR/GBP']


def symbol(pair):
    return pair.replace('/', '') + '=X'


def pip_factor(pair):
    return 100 if 'JPY' in pair else 10000


def fmt(value, pair):
    return format(value, '.' + ('3' if 'JPY' in pair else '5') + 'f')


def fetch_1m(sym):
    return yf.Ticker(sym).history(period='1d', interval='1m')


def latest_price(sym):
    df = fetch_1m(sym)
    if df is None or df.empty:
        return None, None
    return float(df['Close'].iloc[-1]), df


def make_chart(df, title, entry=None):
    try:
        import mplfinance as mpf
        path = os.path.join(tempfile.gettempdir(), 'chart_' + str(random.randint(1000, 9999999)) + '.png')
        mc = mpf.make_marketcolors(up='#00E676', down='#FF5252', edge='inherit', wick='inherit')
        style = mpf.make_mpf_style(marketcolors=mc, base_mpf_style='nightclouds', gridstyle=':')
        kwargs = dict(type='candle', style=style, title=title,
                      savefig=dict(fname=path, dpi=120, bbox_inches='tight'), volume=False)
        if entry is not None:
            kwargs['hlines'] = dict(hlines=[entry], colors=['#FFD600'], linestyle='--', linewidths=1)
        mpf.plot(df.tail(15), **kwargs)
        return path
    except Exception as e:
        log.warning('chart error: %s', e)
        return None


def scan_market():
    rows = []
    for pair in SCAN_PAIRS:
        try:
            df = fetch_1m(symbol(pair))
            if df is None or len(df) < 30:
                rows.append({'pair': pair, 'ok': False})
                continue
            f = pip_factor(pair)
            close = df['Close']
            rng = (float(df['High'].tail(15).max()) - float(df['Low'].tail(15).min())) * f
            e9 = float(close.ewm(span=9, adjust=False).mean().iloc[-1])
            e21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
            gap = (e9 - e21) * f
            if gap > 0.3:
                trend = 'ওপরের দিকে ↑'
            elif gap < -0.3:
                trend = 'নিচের দিকে ↓'
            else:
                trend = 'সাইডওয়েজ ↔'
            good = (3 <= rng <= 15) and abs(gap) > 0.3
            rows.append({'pair': pair, 'ok': True, 'price': float(close.iloc[-1]), 'rng': rng,
                         'trend': trend, 'gap': gap, 'good': good})
        except Exception as e:
            log.warning('scan error %s: %s', pair, e)
            rows.append({'pair': pair, 'ok': False})
    return rows


def scan_text(rows):
    lines = ['⚡ *Market Scan (লাইভ ডেটা)*', '']
    best = None
    for r in rows:
        if not r['ok']:
            lines.append('⚪ ' + r['pair'] + ': ডেটা নেই')
            continue
        if r['good']:
            verdict = '✅ ভালো'
            if best is None or abs(r['gap']) > abs(best['gap']):
                best = r
        elif r['rng'] < 3:
            verdict = '😴 খুব শান্ত'
        elif r['rng'] > 15:
            verdict = '⚠️ অস্থির'
        else:
            verdict = '⏳ সাইডওয়েজ'
        lines.append(r['pair'] + ': ' + fmt(r['price'], r['pair']) + ' | ' + format(r['rng'], '.1f') + ' pips | ' + r['trend'] + ' | ' + verdict)
    lines.append('')
    if best:
        lines.append('👉 *সিদ্ধান্ত:* এখন ' + best['pair'] + ' এ মুভমেন্ট আর ট্রেন্ড সবচেয়ে পরিষ্কার (' + best['trend'] + ')।')
    else:
        lines.append('👉 *সিদ্ধান্ত:* এখন পরিষ্কার সেটআপ নেই, অপেক্ষা করা ভালো।')
    lines.append('')
    lines.append('এটা EMA 9/21 আর মুভমেন্ট দেখে হিসাব, সিগন্যাল বা লাভের গ্যারান্টি না।')
    return NL.join(lines)


# ---------------- UI ----------------
B1 = '1. Compound'
B2 = '2. Monthly Calculator'
B3 = '3. News & Decision'
B4 = '4. Unlimited Music & Vibe'
B5 = '5. 📊 Live Charts Console'
B6 = '6. ⚡ Auto Best Time Finder'
B7 = '7. 📌 Permanent Trading Bar'
B8 = '8. 📈 Profit/Loss Stats'
B9 = '9. 💪 Confidence Boost'
B10 = '10. ⚠️ Overconfidence Control'
B11 = '11. 🎯 Real Market Backtest'
B12 = '12. 🤖 Bot Control'
B13 = '13. Risk Management & Settings'
BACK = '🔙 Back to Menu'
MENU_LABELS = [B1, B2, B3, B4, B5, B6, B7, B8, B9, B10, B11, B12, B13, BACK]


def make_box():
    now = now_ist()
    h = now.hour
    if 5 <= h < 13:
        session = 'Asian Session 🇯🇵'
    elif 13 <= h < 18:
        session = 'London Session 🇪🇺'
    elif 18 <= h < 23:
        session = 'New York Session 🇺🇸'
    else:
        session = 'Pacific/Late Session 🌊'
    d = now.strftime('%Y-%m-%d')
    t = now.strftime('%I:%M:%S %p IST')
    return NL.join([
        '┌──────────────────────────┐',
        '│ 📅 ' + format(d, '<21') + ' │',
        '│ ⏰ ' + format(t, '<21') + ' │',
        '│ 🌐 ' + format(session, '<21') + ' │',
        '└──────────────────────────┘',
    ]) + NL + NL


def main_keyboard():
    rows = [[B1, B2], [B3, B4], [B5, B6], [B7, B8], [B9, B10], [B11, B12], [B13]]
    return ReplyKeyboardMarkup([[KeyboardButton(x) for x in r] for r in rows], resize_keyboard=True)


def pair_keyboard():
    rows = [['EUR/USD', 'GBP/USD'], ['AUD/USD', 'USD/JPY'], ['EUR/GBP', 'USD/CAD'], [BACK]]
    return ReplyKeyboardMarkup([[KeyboardButton(x) for x in r] for r in rows], resize_keyboard=True)


def pair_markup():
    rows = []
    for i in range(0, len(PAIRS), 2):
        rows.append([InlineKeyboardButton(p, callback_data='pair|' + p) for p in PAIRS[i:i + 2]])
    return InlineKeyboardMarkup(rows)


async def send(context, chat_id, text, reply_markup=None, plain=False):
    if not plain:
        try:
            return await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown',
                                                  reply_markup=reply_markup)
        except BadRequest:
            pass
    return await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)


async def say(update, text, reply_markup=None, plain=False):
    msg = update.effective_message
    if not plain:
        try:
            return await msg.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)
        except BadRequest:
            pass
    return await msg.reply_text(text, reply_markup=reply_markup)


async def safe_edit(query, text, reply_markup=None):
    try:
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    except BadRequest as e:
        if 'not modified' in str(e).lower():
            return
        try:
            await query.edit_message_text(text, reply_markup=reply_markup)
        except BadRequest:
            pass


async def send_menu(update, context):
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=make_box() + '🤖 Quotex Risk Management Bot চালু! নিচের মেনু থেকে বাছাই করো:',
        reply_markup=main_keyboard())


# ---------------- VOICE ----------------
def make_voice_file(text):
    path = os.path.join(tempfile.gettempdir(), 'voice_' + str(random.randint(1000, 9999999)) + '.mp3')
    gTTS(text=text, lang='bn', slow=False).save(path)
    return path


async def send_voice_text(context, chat_id, text):
    try:
        path = await asyncio.to_thread(make_voice_file, text)
        with open(path, 'rb') as vf:
            await context.bot.send_voice(chat_id=chat_id, voice=vf)
        os.remove(path)
    except Exception as e:
        log.warning('voice error: %s', e)


# ---------------- SECURITY GATE ----------------
security_state = {}


async def gate(update, context):
    user = update.effective_user
    if user is None:
        return
    msg = update.effective_message
    if OWNER_ID is not None and user.id != OWNER_ID:
        if msg:
            await msg.reply_text('🔒 এই বট প্রাইভেট।')
        raise ApplicationHandlerStop
    if not PASSWORDS:
        return
    state = security_state.get(user.id)
    if state == 'ok':
        return
    if update.callback_query:
        await update.callback_query.answer('🔒 আগে /start দিয়ে পাসওয়ার্ড দাও', show_alert=True)
        raise ApplicationHandlerStop
    if msg is None or not msg.text:
        raise ApplicationHandlerStop
    text = msg.text.strip()
    chat_id = update.effective_chat.id
    if state is None or text.startswith('/start'):
        security_state[user.id] = 0
        await msg.reply_text('🔐 পাসওয়ার্ড ১ দাও:')
        raise ApplicationHandlerStop
    try:
        await msg.delete()
    except Exception:
        pass
    if text == PASSWORDS[state]:
        state += 1
        if state >= len(PASSWORDS):
            security_state[user.id] = 'ok'
            await send_menu(update, context)
        else:
            security_state[user.id] = state
            await context.bot.send_message(chat_id=chat_id, text='✅ ঠিক আছে। পাসওয়ার্ড ' + str(state + 1) + ' দাও:')
    else:
        security_state.pop(user.id, None)
        await context.bot.send_message(chat_id=chat_id, text='❌ ভুল পাসওয়ার্ড। আবার /start দাও।')
    raise ApplicationHandlerStop


# ---------------- SESSION + TRADING BAR ----------------
def trade_params(chat_id):
    st = SETTINGS.setdefault(chat_id, {'amount': 2.0, 'payout': 85.0})
    plan = PLANS.get(chat_id)
    stake = plan['stake'] if plan else st['amount']
    return stake, st['payout']


def get_session(chat_id, pair):
    s = SESSIONS.get(chat_id)
    if s is None:
        s = {'start': now_ist(), 'trades': 0, 'wins': 0, 'losses': 0, 'draws': 0, 'net': 0.0,
             'pair': pair, 'closed': False, 'warned': False, 'busy': False}
        SESSIONS[chat_id] = s
    return s


def bar_text(chat_id, price_line=''):
    s = SESSIONS.get(chat_id)
    plan = PLANS.get(chat_id)
    lines = [make_box() + '📌 *LIVE TRADING BAR*']
    if s:
        mins = int((now_ist() - s['start']).total_seconds() // 60)
        lines.append('🎯 Pair: ' + s['pair'] + '  |  🕐 শুরু ' + s['start'].strftime('%I:%M %p') + ' (' + str(mins) + ' মিনিট)')
        lines.append('🟢 Win: ' + str(s['wins']) + ' | 🔴 Loss: ' + str(s['losses']) + ' | ট্রেড: ' + str(s['trades']) + '/' + str(MAX_TRADES))
        lines.append('💵 সেশন Net: $' + format(s['net'], '.2f'))
    if plan:
        lines.append('🎯 TP $' + format(plan['target_amt'], '.2f') + ' | 🛑 SL $' + format(plan['sl_amt'], '.2f') + ' | Entry $' + format(plan['stake'], '.2f'))
    if price_line:
        lines.append(price_line)
    lines.append('-----------------------------------')
    if s and s['closed']:
        lines.append('🛑 এই সেশন বন্ধ। নতুন সেশন শুরু করতে সময় লাগলে বাটন চাপো।')
    else:
        lines.append('⚡ দিক বাছাই করো, ১ মিনিট পর রেজাল্ট আসবে:')
    return NL.join(lines)


def bar_markup(chat_id):
    s = SESSIONS.get(chat_id)
    if s is None:
        return pair_markup()
    if s['closed']:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton('🔄 নতুন সেশন', callback_data='sess_new')],
            [InlineKeyboardButton('📊 Stats', callback_data='bar_stats')],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🟢 UP (CALL)', callback_data='bar_up'),
         InlineKeyboardButton('🔴 DOWN (PUT)', callback_data='bar_down')],
        [InlineKeyboardButton('🔄 পেয়ার বদলাও', callback_data='chg_pair')],
        [InlineKeyboardButton('📊 Stats', callback_data='bar_stats'),
         InlineKeyboardButton('🛑 STOP', callback_data='bar_stop')],
    ])


async def send_bar(context, chat_id, price_line=''):
    await send(context, chat_id, bar_text(chat_id, price_line), bar_markup(chat_id))


async def open_bar(update, context):
    ALERT_CHATS.add(update.effective_chat.id)
    await say(update, '📌 *Permanent Trading Bar*' + NL + NL + 'কোন পেয়ারে ট্রেড করবে? বাছাই করলে লাইভ ট্র্যাক শুরু হবে:', pair_markup())


async def start_trade(q, context, chat_id, direction):
    s = SESSIONS.get(chat_id)
    if s is None:
        await q.answer('আগে পেয়ার বাছাই করো', show_alert=True)
        return
    if s['closed']:
        await q.answer('এই সেশন বন্ধ, নতুন সেশন শুরু করো', show_alert=True)
        return
    if s['busy']:
        await q.answer('একটা ট্রেড চলছে, রেজাল্টের অপেক্ষা করো', show_alert=True)
        return
    pair = s['pair']
    await q.answer('এন্ট্রি নিচ্ছি...')
    price, _ = await asyncio.to_thread(latest_price, symbol(pair))
    if price is None:
        await send(context, chat_id, '❌ লাইভ প্রাইস পাওয়া যাচ্ছে না (মার্কেট বন্ধ থাকতে পারে)।', plain=True)
        return
    s['busy'] = True
    await send(context, chat_id, '⏱ ' + pair + ' ' + direction + ' এন্ট্রি @ ' + fmt(price, pair) + NL + '১ মিনিট পর রেজাল্ট আসবে...', plain=True)
    asyncio.create_task(finish_trade(context, chat_id, pair, direction, price, s))


def limit_messages(chat_id, s):
    plan = PLANS.get(chat_id)
    msgs = []
    if plan and s['net'] >= plan['target_amt']:
        msgs.append('🎯 Take Profit হিট হয়ে গেছে! আজকের মতো ট্রেড বন্ধ করো, মার্কেট বন্ধ করে দাও।')
    if plan and s['net'] <= -plan['sl_amt']:
        msgs.append('🛑 Stoploss হিট হয়ে গেছে! আজ আর ট্রেড নয়, মার্কেট বন্ধ করো।')
    if s['trades'] >= MAX_TRADES:
        msgs.append('✋ এই সেশনে সর্বোচ্চ ' + str(MAX_TRADES) + ' টা ট্রেড হয়ে গেছে। এখন থামো, বিশ্রাম নাও।')
    return msgs


async def finish_trade(context, chat_id, pair, direction, entry, s):
    try:
        await asyncio.sleep(TRADE_SECONDS)
        exit_price, df = await asyncio.to_thread(latest_price, symbol(pair))
        if exit_price is None:
            await send(context, chat_id, '⚠️ রেজাল্টের প্রাইস পাওয়া যায়নি, এই ট্রেড গণনা হয়নি।', plain=True)
            return
        diff = exit_price - entry
        if abs(diff) < 1e-9:
            result = 'DRAW'
        elif (diff > 0 and direction == 'UP') or (diff < 0 and direction == 'DOWN'):
            result = 'WIN'
        else:
            result = 'LOSS'
        stake, payout = trade_params(chat_id)
        if result == 'WIN':
            pnl = stake * payout / 100
        elif result == 'LOSS':
            pnl = -stake
        else:
            pnl = 0.0
        icon = {'WIN': '✅ WIN', 'LOSS': '❌ LOSS', 'DRAW': '➖ DRAW'}[result]
        caption = (pair + ' ' + direction + ' | ' + icon + NL + 'Entry ' + fmt(entry, pair) + ' → Exit ' + fmt(exit_price, pair)
                   + NL + 'P/L: $' + format(pnl, '.2f'))
        path = await asyncio.to_thread(make_chart, df, pair + ' ' + direction + ' ' + result, entry)
        photo_id = None
        if path:
            with open(path, 'rb') as f:
                m = await context.bot.send_photo(chat_id=chat_id, photo=f, caption=caption)
            photo_id = m.photo[-1].file_id
            try:
                os.remove(path)
            except Exception:
                pass
        else:
            await send(context, chat_id, caption, plain=True)
        update_trade_data(result, pnl, {'pair': pair, 'dir': direction, 'entry': entry, 'exit': exit_price, 'photo': photo_id})
        s['trades'] += 1
        if result == 'WIN':
            s['wins'] += 1
        elif result == 'LOSS':
            s['losses'] += 1
        else:
            s['draws'] += 1
        s['net'] = round(s['net'] + pnl, 2)
        if result == 'WIN':
            alert = next_text('win') + ' 🚀💰'
            voice = alert
        elif result == 'LOSS':
            alert = next_text('loss') + ' ☕🧊'
            voice = alert
        else:
            alert = 'প্রাইস একই জায়গায় থেকে গেছে, ড্র হয়েছে। কোনো লাভ বা লস নেই।'
            voice = alert
        await send(context, chat_id, alert, plain=True)
        await send_voice_text(context, chat_id, voice)
        msgs = limit_messages(chat_id, s)
        if msgs:
            s['closed'] = True
            for t in msgs:
                await send(context, chat_id, t, plain=True)
        await send_bar(context, chat_id)
    except Exception as e:
        log.warning('finish_trade error: %s', e)
        try:
            await send(context, chat_id, '⚠️ রেজাল্ট বের করতে সমস্যা হয়েছে।', plain=True)
        except Exception:
            pass
    finally:
        s['busy'] = False


# ---------------- HISTORY ----------------
async def send_history(context, chat_id, days):
    data = load_json(DATA_FILE)
    items = []
    for i in range(days):
        d = (now_ist() - datetime.timedelta(days=i)).strftime('%Y-%m-%d')
        r = data.get(d)
        if isinstance(r, dict):
            for h in r.get('history', []):
                items.append((d, h))
    items.sort(key=lambda x: (x[0], x[1].get('time', '')))
    items = items[-10:]
    if not items:
        await send(context, chat_id, 'কোনো ট্রেড হিস্ট্রি নেই।', plain=True)
        return
    for d, h in items:
        cap = (d + ' ' + h.get('time', '') + ' | ' + str(h.get('pair', '')) + ' ' + str(h.get('dir', '')) + ' | '
               + str(h.get('result', '')) + ' | $' + format(h.get('pnl', 0.0), '.2f'))
        photo = h.get('photo')
        try:
            if photo:
                await context.bot.send_photo(chat_id=chat_id, photo=photo, caption=cap)
            else:
                await send(context, chat_id, cap, plain=True)
        except Exception as e:
            log.warning('history send error: %s', e)


async def show_stats(update, context):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton('📜 আজকের হিস্ট্রি (ছবিসহ)', callback_data='hist|1'),
                                InlineKeyboardButton('📜 ৭ দিনের হিস্ট্রি', callback_data='hist|7')]])
    await say(update, stats_text(), kb)


# ---------------- CALLBACKS ----------------
async def on_callback(update, context):
    q = update.callback_query
    data = q.data or ''
    chat_id = q.message.chat_id
    if data.startswith('pair|'):
        pair = data.split('|', 1)[1]
        s = get_session(chat_id, pair)
        s['pair'] = pair
        ALERT_CHATS.add(chat_id)
        await q.answer()
        price, _ = await asyncio.to_thread(latest_price, symbol(pair))
        if price is not None:
            pl = '💱 লাইভ প্রাইস: ' + fmt(price, pair)
        else:
            pl = '⚠️ লাইভ প্রাইস পাওয়া যাচ্ছে না (মার্কেট বন্ধ থাকতে পারে)'
        await safe_edit(q, bar_text(chat_id, pl), bar_markup(chat_id))
    elif data == 'chg_pair':
        await q.answer()
        await safe_edit(q, '📌 কোন পেয়ারে ট্রেড করবে?', pair_markup())
    elif data in ('bar_up', 'bar_down'):
        await start_trade(q, context, chat_id, 'UP' if data == 'bar_up' else 'DOWN')
    elif data == 'bar_stats':
        s = SESSIONS.get(chat_id)
        if s:
            txt = 'Win ' + str(s['wins']) + ' | Loss ' + str(s['losses']) + ' | Net $' + format(s['net'], '.2f')
        else:
            txt = 'কোনো সেশন চলছে না'
        await q.answer(txt, show_alert=True)
    elif data == 'bar_stop':
        await q.answer()
        s = SESSIONS.pop(chat_id, None)
        if s:
            txt = ('🛑 সেশন বন্ধ।' + NL + 'Win ' + str(s['wins']) + ' | Loss ' + str(s['losses'])
                   + ' | Net $' + format(s['net'], '.2f'))
        else:
            txt = '🛑 সেশন বন্ধ।'
        await safe_edit(q, txt)
    elif data == 'sess_new':
        SESSIONS.pop(chat_id, None)
        await q.answer()
        await safe_edit(q, '📌 নতুন সেশন! কোন পেয়ারে ট্রেড করবে?', pair_markup())
    elif data == 'ctl_on':
        ALERT_CHATS.add(chat_id)
        await q.answer('✅ Volatility alert চালু')
    elif data == 'ctl_off':
        ALERT_CHATS.discard(chat_id)
        await q.answer('⏸ Volatility alert বন্ধ')
    elif data == 'ctl_bar':
        await q.answer()
        ALERT_CHATS.add(chat_id)
        await send(context, chat_id, '📌 কোন পেয়ারে ট্রেড করবে?', pair_markup(), plain=True)
    elif data.startswith('mus|'):
        await q.answer()
        await send_category(context, chat_id, int(data.split('|', 1)[1]))
    elif data.startswith('msave|'):
        idx = int(data.split('|', 1)[1])
        song = context.user_data.pop('song', None)
        if not song:
            await q.answer('গান পাওয়া যায়নি, আবার পাঠাও', show_alert=True)
            return
        lib = load_json(MUSIC_FILE)
        lib.setdefault(str(idx), []).append(song)
        save_json(MUSIC_FILE, lib)
        await q.answer('সেভ হয়েছে')
        await safe_edit(q, '✅ সেভ: ' + song['title'] + NL + 'ক্যাটাগরি: ' + CATS[idx])
    elif data.startswith('txt|'):
        await q.answer()
        await send_text_kind(context, chat_id, data.split('|', 1)[1])
    elif data.startswith('hist|'):
        await q.answer()
        await send_history(context, chat_id, int(data.split('|', 1)[1]))
    else:
        await q.answer()


# ---------------- SIMPLE MENU ITEMS ----------------
NEWS_LINES = [
    '==================================',
    '🔥 নিউজ অনুযায়ী ট্রেড ডিসিশন রুল 🔥',
    '==================================',
    '',
    '👑 NFP ➔ 📈 শুধুমাত্র TREND FOLLOW',
    '(S&R সম্পূর্ণ ফেল 🚫 | ট্রেন্ডের দিকেই ট্রেড)',
    '',
    '📉 CPI ➔ 🚀 শুধুমাত্র BREAKOUT & TREND',
    '(S&R বাউন্স কাজ করবে না 🚫 | ব্রেকআউটের দিকে ট্রেড)',
    '',
    '🏭 PPI ➔ ↔️ SUPPORT & RESISTANCE (S&R)',
    '(নিউজ পার হওয়ার ৩০ মি. পর S&R চমৎকার কাজ করবে ✅)',
    '',
    '🏛️ FOMC ➔ ⛔ NO TRADE ZONE',
    '(S&R বা Trend কোনোটাই কাজ করে না 🚫 | ট্রেড বন্ধ)',
    '==================================',
    '💡 মূল কথা: NFP & CPI = ট্রেন্ড | PPI = সাপোর্ট/রেসিস্ট্যান্স | FOMC = অফ',
]


async def show_news(update, context):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton('📅 Forex Factory Calendar', url='https://www.forexfactory.com/calendar')]])
    await say(update, NL.join(NEWS_LINES), kb, plain=True)


async def show_charts(update, context):
    def url(sym):
        return 'https://s.tradingview.com/widgetembed/?symbol=' + sym + '&interval=1&theme=dark'
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('🇪🇺/🇺🇸 EUR/USD', web_app=WebAppInfo(url=url('FX:EURUSD'))),
         InlineKeyboardButton('🇬🇧/🇺🇸 GBP/USD', web_app=WebAppInfo(url=url('FX:GBPUSD')))],
        [InlineKeyboardButton('🇺🇸/🇯🇵 USD/JPY', web_app=WebAppInfo(url=url('FX:USDJPY'))),
         InlineKeyboardButton('🇺🇸/🇨🇦 USD/CAD', web_app=WebAppInfo(url=url('FX:USDCAD')))],
        [InlineKeyboardButton('🥇 GOLD (XAU/USD)', web_app=WebAppInfo(url=url('OANDA:XAUUSD')))],
    ])
    await say(update, make_box() + '📊 *Live Chart Console*' + NL + NL + 'পছন্দের Pair বাছাই করো:', kb)


async def show_best_time(update, context):
    await say(update, '⏳ সব পেয়ারের লাইভ ডেটা দেখছি, কয়েক সেকেন্ড লাগবে...', plain=True)
    try:
        rows = await asyncio.to_thread(scan_market)
    except Exception as e:
        log.warning('scan failed: %s', e)
        rows = []
    if not rows or not any(r['ok'] for r in rows):
        await say(update, '❌ এখন লাইভ ডেটা পাওয়া যাচ্ছে না (মার্কেট বন্ধ থাকতে পারে, যেমন শনি-রবিবার)।', plain=True)
        return
    await say(update, scan_text(rows))


async def send_text_kind(context, chat_id, kind):
    txt = next_text(kind)
    icon = '💪' if kind == 'conf' else '⚠️'
    kb = InlineKeyboardMarkup([[InlineKeyboardButton('🔁 আরেকটা', callback_data='txt|' + kind)]])
    await send(context, chat_id, icon + ' ' + txt, kb, plain=True)


async def show_conf(update, context):
    await send_text_kind(context, update.effective_chat.id, 'conf')


async def show_over(update, context):
    await send_text_kind(context, update.effective_chat.id, 'over')


async def show_control(update, context):
    on = update.effective_chat.id in ALERT_CHATS
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('▶️ Alert ON', callback_data='ctl_on'),
         InlineKeyboardButton('⏸ Alert OFF', callback_data='ctl_off')],
        [InlineKeyboardButton('📌 Trading Bar খোলো', callback_data='ctl_bar')],
    ])
    status = 'চালু ✅' if on else 'বন্ধ ⏸'
    await say(update, '🤖 *Bot Control*' + NL + NL + 'Volatility alert এখন: ' + status, kb)


async def show_risk(update, context):
    chat_id = update.effective_chat.id
    stake, payout = trade_params(chat_id)
    be = 100 / (100 + payout) * 100
    lines = [
        '🛡 *Risk Management & Settings*',
        '',
        '- প্রতি ট্রেডে ব্যালেন্সের ১-২% এর বেশি নয়',
        '- দৈনিক স্টপলস ছুঁলে ওই দিন বন্ধ',
        '- সেশনে সর্বোচ্চ ' + str(MAX_TRADES) + ' ট্রেড, সর্বোচ্চ ' + str(SESSION_LIMIT_MIN) + ' মিনিট',
        '- ব্রেক-ইভেন Win Rate: payout ' + format(payout, '.0f') + '% হলে প্রায় ' + format(be, '.1f') + '%',
        '',
        '*এখনকার সেটিং:* Entry $' + format(stake, '.2f') + ' | Payout ' + format(payout, '.0f') + '%',
        '',
        'বদলাতে লেখো: /amount 5  অথবা  /payout 85',
    ]
    await say(update, NL.join(lines))


def parse_num(text):
    table = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')
    t = text.translate(table).replace(',', '').replace('%', '').replace('$', '').strip()
    try:
        return float(t)
    except ValueError:
        return None


async def cmd_amount(update, context):
    val = parse_num(context.args[0]) if context.args else None
    if val is None or val <= 0:
        await say(update, 'ব্যবহার: /amount 5', plain=True)
        return
    SETTINGS.setdefault(update.effective_chat.id, {'amount': 2.0, 'payout': 85.0})['amount'] = val
    await say(update, '✅ Entry সেট: $' + format(val, '.2f') + ' (Quotex প্ল্যান থাকলে প্ল্যানের ২% এন্ট্রিই ব্যবহার হবে)', plain=True)


async def cmd_payout(update, context):
    val = parse_num(context.args[0]) if context.args else None
    if val is None or not (1 <= val <= 100):
        await say(update, 'ব্যবহার: /payout 85', plain=True)
        return
    SETTINGS.setdefault(update.effective_chat.id, {'amount': 2.0, 'payout': 85.0})['payout'] = val
    await say(update, '✅ Payout সেট: ' + format(val, '.0f') + '%', plain=True)


# ---------------- MUSIC LIBRARY ----------------
CATS = ['🎵 বাংলা রোমান্টিক', '😢 বাংলা স্যাড', '💕 হিন্দি রোমান্টিক', '💔 হিন্দি স্যাড', '🎶 ভোজপুরি', '😎 Aura', '🔥 Motivation', '🌿 Relax']
KEYWORDS = [['bangla romantic', 'bengali romantic', 'বাংলা রোমান্টিক'], ['bangla sad', 'bengali sad', 'বাংলা স্যাড'], ['hindi romantic'], ['hindi sad'],
            ['bhojpuri', 'ভোজপুরি'], ['aura'], ['motivat'], ['relax', 'lofi', 'chill']]


def guess_category(title):
    low = title.lower()
    for i, words in enumerate(KEYWORDS):
        for w in words:
            if w in low:
                return i
    return None


async def show_music(update, context):
    lib = load_json(MUSIC_FILE)
    rows = []
    for i in range(0, len(CATS), 2):
        row = []
        for k in range(i, min(i + 2, len(CATS))):
            n = len(lib.get(str(k), []))
            row.append(InlineKeyboardButton(CATS[k] + ' (' + str(n) + ')', callback_data='mus|' + str(k)))
        rows.append(row)
    text = ('🎶 Music Library' + NL + NL + 'ক্যাটাগরি চাপলে গানের নাম আর অডিও আসবে।' + NL
            + 'নতুন গান যোগ করতে গানের অডিও ফাইল এখানে পাঠাও, আমি ক্যাটাগরি জিজ্ঞেস করবো।')
    await say(update, text, InlineKeyboardMarkup(rows), plain=True)


async def on_audio(update, context):
    m = update.message
    if m.audio:
        fid = m.audio.file_id
        kind = 'audio'
        title = m.audio.title or m.audio.file_name or 'গান'
        if m.audio.performer and m.audio.title:
            title = m.audio.performer + ' - ' + m.audio.title
    else:
        fid = m.document.file_id
        kind = 'document'
        title = m.document.file_name or 'গান'
    if '.' in title:
        title = title.rsplit('.', 1)[0]
    context.user_data['song'] = {'file_id': fid, 'kind': kind, 'title': title}
    guess = guess_category(title)
    buttons = []
    for i, name in enumerate(CATS):
        label = ('⭐ ' if i == guess else '') + name
        buttons.append(InlineKeyboardButton(label, callback_data='msave|' + str(i)))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    await say(update, '🎵 গান পেয়েছি: ' + title + NL + 'কোন ক্যাটাগরিতে রাখবো?', InlineKeyboardMarkup(rows), plain=True)


async def send_category(context, chat_id, idx):
    lib = load_json(MUSIC_FILE)
    songs = lib.get(str(idx), [])
    if not songs:
        await send(context, chat_id, CATS[idx] + ': এখনো কোনো গান নেই। গানের অডিও ফাইল পাঠাও।', plain=True)
        return
    picks = random.sample(songs, min(10, len(songs)))
    names = NL.join(str(n + 1) + '. ' + p['title'] for n, p in enumerate(picks))
    kb = InlineKeyboardMarkup([[InlineKeyboardButton('🔀 আরও গান', callback_data='mus|' + str(idx))]])
    await send(context, chat_id, CATS[idx] + ' (' + str(len(songs)) + ' টা গান)' + NL + names, kb, plain=True)
    for p in picks:
        try:
            if p.get('kind') == 'audio':
                await context.bot.send_audio(chat_id=chat_id, audio=p['file_id'], caption=p['title'])
            else:
                await context.bot.send_document(chat_id=chat_id, document=p['file_id'], caption=p['title'])
        except Exception as e:
            log.warning('music send error: %s', e)


# ---------------- CALCULATORS (CONVERSATIONS) ----------------
(FX_BAL, QX_TP, QX_SL, QX_BAL, MN_TP, MN_BAL, BT_PAIR) = range(7)
NUM_FILTER = filters.TEXT & ~filters.COMMAND & ~filters.Text(MENU_LABELS)
WD = ['সোম', 'মঙ্গল', 'বুধ', 'বৃহ', 'শুক্র', 'শনি', 'রবি']


async def show_compound_menu(update, context):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('🌍 Forex / Crypto', callback_data='c1_fx')],
        [InlineKeyboardButton('📊 Quotex', callback_data='c1_qx')],
    ])
    await say(update, make_box() + '🧮 *Compound & Money Management*' + NL + NL + 'কোনটার জন্য হিসাব করবে?', kb)


async def fx_start(update, context):
    q = update.callback_query
    await q.answer()
    await send(context, q.message.chat_id, '🌍 *Forex/Crypto Calculator*' + NL + NL + 'তোমার অ্যাকাউন্ট ব্যালেন্স কত $? (যেমন 100):')
    return FX_BAL


async def fx_bal(update, context):
    bal = parse_num(update.message.text)
    if bal is None or bal <= 0:
        await say(update, '❌ সঠিক সংখ্যা লেখো (যেমন 100):', plain=True)
        return FX_BAL
    risk = bal * 0.01
    lines = [
        '🌍 *Forex/Crypto Result*',
        '',
        '💼 ব্যালেন্স: $' + format(bal, '.2f'),
        'ধরে নিয়েছি: প্রতি ট্রেডে ঝুঁকি ১% আর টার্গেট ১:২',
        '',
        '🛑 Stoploss (ঝুঁকি): $' + format(risk, '.2f'),
        '🎯 Take Profit: $' + format(risk * 2, '.2f'),
        '',
        '*Forex লট সাইজ* (EUR/USD ধরনের, ১ লটে প্রতি pip $10):',
    ]
    for sl in (10, 20, 30):
        lot = risk / (sl * 10)
        note = '  (০.০১ এর কম, ঝুঁকি বেশি হবে)' if lot < 0.01 else ''
        lines.append('- SL ' + str(sl) + ' pips: ' + format(lot, '.2f') + ' লট' + note)
    lines += ['', '*Crypto পজিশন সাইজ:*']
    for pct in (0.5, 1, 2):
        lines.append('- SL ' + format(pct, 'g') + '% দূরে: $' + format(risk / (pct / 100), '.2f'))
    lines += ['', 'অন্য পেয়ারে pip value আলাদা হতে পারে। এটা হিসাব, লাভের গ্যারান্টি না।']
    await say(update, NL.join(lines), main_keyboard())
    return ConversationHandler.END


async def qx_start(update, context):
    q = update.callback_query
    await q.answer()
    await send(context, q.message.chat_id, '📊 *Quotex Calculator*' + NL + NL + '১. Take Profit কত %? (যেমন 5):')
    return QX_TP


async def qx_tp(update, context):
    v = parse_num(update.message.text)
    if v is None or v <= 0:
        await say(update, '❌ সঠিক সংখ্যা লেখো (যেমন 5):', plain=True)
        return QX_TP
    context.user_data['qx'] = {'tp': v}
    await say(update, '২. Stoploss কত %? (যেমন 3):', plain=True)
    return QX_SL


async def qx_sl(update, context):
    v = parse_num(update.message.text)
    if v is None or v <= 0:
        await say(update, '❌ সঠিক সংখ্যা লেখো (যেমন 3):', plain=True)
        return QX_SL
    context.user_data['qx']['sl'] = v
    await say(update, '৩. তোমার ব্যালেন্স কত $? (যেমন 100):', plain=True)
    return QX_BAL


async def qx_bal(update, context):
    bal = parse_num(update.message.text)
    if bal is None or bal <= 0:
        await say(update, '❌ সঠিক সংখ্যা লেখো (যেমন 100):', plain=True)
        return QX_BAL
    chat_id = update.effective_chat.id
    c = context.user_data['qx']
    stake = round(bal * 0.02, 2)
    target_amt = bal * c['tp'] / 100
    sl_amt = bal * c['sl'] / 100
    payout = SETTINGS.setdefault(chat_id, {'amount': 2.0, 'payout': 85.0})['payout']
    win_profit = stake * payout / 100
    wins_needed = math.ceil(target_amt / win_profit) if win_profit > 0 else 0
    losses_to_sl = math.ceil(sl_amt / stake) if stake > 0 else 0
    PLANS[chat_id] = {'target_amt': target_amt, 'sl_amt': sl_amt, 'stake': stake, 'bal': bal}
    lines = [
        '📊 *Quotex Plan সেভ হয়েছে*',
        '',
        '💼 ব্যালেন্স: $' + format(bal, '.2f'),
        '💲 প্রতি এন্ট্রি (২%): $' + format(stake, '.2f'),
        '🎯 Take Profit: $' + format(target_amt, '.2f') + ' (' + format(c['tp'], 'g') + '%)',
        '🛑 Stoploss: $' + format(sl_amt, '.2f') + ' (' + format(c['sl'], 'g') + '%)',
        '',
        '✅ TP ছুঁতে লাগবে প্রায় ' + str(wins_needed) + ' টা Win (payout ' + format(payout, '.0f') + '%)',
        '❌ SL ছুঁতে লাগবে প্রায় ' + str(losses_to_sl) + ' টা Loss',
        '',
        '*নিয়ম:*',
        '- TP বা SL হিট হলে আমি জানাবো',
        '- সেশনে সর্বোচ্চ ' + str(MAX_TRADES) + ' টা ট্রেড',
        '- ১ ঘণ্টা হলে বাড়ি যেতে বলবো',
        '',
        'এরপর ' + B7 + ' চাপো।',
    ]
    await say(update, NL.join(lines), main_keyboard())
    return ConversationHandler.END


async def mn_start(update, context):
    await say(update, make_box() + '📅 *Monthly Calculator*' + NL + NL + '১. মাসিক Take Profit কত %? (যেমন 20):')
    return MN_TP


async def mn_tp(update, context):
    v = parse_num(update.message.text)
    if v is None or v <= 0:
        await say(update, '❌ সঠিক সংখ্যা লেখো (যেমন 20):', plain=True)
        return MN_TP
    context.user_data['mn'] = {'tp': v}
    await say(update, '২. তোমার অ্যাকাউন্ট ব্যালেন্স কত $? (যেমন 100):', plain=True)
    return MN_BAL


async def mn_bal(update, context):
    bal = parse_num(update.message.text)
    if bal is None or bal <= 0:
        await say(update, '❌ সঠিক সংখ্যা লেখো (যেমন 100):', plain=True)
        return MN_BAL
    tp = context.user_data['mn']['tp']
    days = 18
    daily = (1 + tp / 100) ** (1.0 / days) - 1
    d = now_ist().date()
    today = d
    cur = bal
    count = 0
    rows = []
    while count < days:
        wd = d.weekday()
        label = d.strftime('%d %b') + ' (' + WD[wd] + ')'
        mark = '  👈 আজ' if d == today else ''
        if wd >= 4:
            why = 'শুক্রবার, ট্রেড অফ' if wd == 4 else 'মার্কেট বন্ধ'
            rows.append('❌ ' + label + ': ' + why + mark)
        else:
            profit = cur * daily
            cur += profit
            count += 1
            rows.append('✅ ' + str(count) + '. ' + label + ': +$' + format(profit, '.2f') + ' → $' + format(cur, '.2f') + mark)
        d += datetime.timedelta(days=1)
    lines = [
        '📅 Monthly Plan (' + str(days) + ' ট্রেডিং দিন)',
        '',
        '💼 শুরু: $' + format(bal, '.2f') + '  🎯 টার্গেট: ' + format(tp, 'g') + '%',
        '📈 দৈনিক টার্গেট প্রায় ' + format(daily * 100, '.2f') + '% (দিনে দিনে বাড়ার হিসাব)',
        '',
    ] + rows + [
        '',
        '🏁 শেষে ব্যালেন্স: $' + format(cur, '.2f'),
        '',
        '⚠️ শুক্রবার, শনি, রবি বাদ। এটা পরিকল্পনার হিসাব, লাভের গ্যারান্টি না।',
    ]
    await say(update, NL.join(lines), main_keyboard(), plain=True)
    return ConversationHandler.END


async def bt_start(update, context):
    await say(update, make_box() + '🎯 *Real Market Backtest*' + NL + NL + 'Pair বাছাই করো:', pair_keyboard())
    return BT_PAIR


async def bt_pair(update, context):
    pair = update.message.text.strip()
    await say(update, '⏳ ' + pair + ' এর ডেটা আনছি...', plain=True)
    try:
        price, df = await asyncio.to_thread(latest_price, symbol(pair))
    except Exception as e:
        log.warning('backtest error: %s', e)
        price, df = None, None
    if price is None or len(df) < 30:
        await say(update, '❌ পর্যাপ্ত মার্কেট ডেটা পাওয়া যায়নি (মার্কেট বন্ধ থাকতে পারে)।', main_keyboard(), plain=True)
        return ConversationHandler.END
    if 'JPY' in pair:
        base = round(price, 0)
        step = 0.5
    else:
        base = round(price, 2)
        step = 0.0025
    lines = [
        '🎯 *' + pair + ' Backtest*',
        '',
        '💱 লাইভ দাম: ' + fmt(price, pair),
        '🟢 আজকের Support (লো): ' + fmt(float(df['Low'].min()), pair),
        '🔴 আজকের Resistance (হাই): ' + fmt(float(df['High'].max()), pair),
        '',
        '*কাছাকাছি Round Number:*',
    ]
    for k in (-2, -1, 0, 1, 2):
        lines.append('- ' + fmt(base + step * k, pair))
    lines += ['', 'এগুলো আজকের ডেটা থেকে হিসাব, ভবিষ্যতের গ্যারান্টি না।']
    await say(update, NL.join(lines), main_keyboard())
    path = await asyncio.to_thread(make_chart, df, pair)
    if path:
        with open(path, 'rb') as f:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=f)
        try:
            os.remove(path)
        except Exception:
            pass
    return ConversationHandler.END


async def cancel(update, context):
    await send_menu(update, context)
    return ConversationHandler.END


# ---------------- ROUTER ----------------
MENU_ACTIONS = {B1: show_compound_menu, B3: show_news, B4: show_music, B5: show_charts, B6: show_best_time,
                B7: open_bar, B8: show_stats, B9: show_conf, B10: show_over, B12: show_control, B13: show_risk}


async def menu_router(update, context):
    text = (update.message.text or '').strip()
    fn = MENU_ACTIONS.get(text)
    if fn:
        await fn(update, context)
    elif text == BACK:
        await send_menu(update, context)
    else:
        await say(update, 'মেনু থেকে একটা বাটন চাপো 👇', main_keyboard(), plain=True)


async def start_cmd(update, context):
    await send_menu(update, context)


# ---------------- BACKGROUND WATCHER ----------------
async def watcher(application):
    tick = 0
    while True:
        await asyncio.sleep(60)
        tick += 1
        try:
            now = now_ist()
            for chat_id, s in list(SESSIONS.items()):
                if s['closed'] or s['warned']:
                    continue
                if (now - s['start']).total_seconds() >= SESSION_LIMIT_MIN * 60:
                    s['warned'] = True
                    s['closed'] = True
                    await application.bot.send_message(
                        chat_id=chat_id,
                        text='⏰ ১ ঘণ্টা হয়ে গেছে! আর ট্রেড নয়, এখন উঠে পড়ো, বাড়ি যাও, বিশ্রাম নাও। 🏠')
            if tick % 10 == 0 and ALERT_CHATS:
                df = await asyncio.to_thread(fetch_1m, 'EURUSD=X')
                if df is not None and len(df) >= 10:
                    std = float(df['Close'].tail(10).std())
                    if std > 0.00030:
                        for chat_id in list(ALERT_CHATS):
                            await application.bot.send_message(
                                chat_id=chat_id,
                                text='🚨 WARNING: মার্কেট হঠাৎ খুব অস্থির! বড় মুভমেন্ট চলছে, সাবধানে থাকো।')
        except Exception as e:
            log.warning('watcher error: %s', e)


async def post_init(application):
    application.bot_data['watcher'] = asyncio.create_task(watcher(application))


def main():
    if not BOT_TOKEN:
        raise SystemExit('BOT_TOKEN environment variable set kora nei')
    keep_alive()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(TypeHandler(Update, gate), group=-1)

    fallbacks = [CommandHandler('cancel', cancel), MessageHandler(filters.Text(MENU_LABELS), cancel)]
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(fx_start, pattern='^c1_fx$')],
        states={FX_BAL: [MessageHandler(NUM_FILTER, fx_bal)]}, fallbacks=fallbacks))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(qx_start, pattern='^c1_qx$')],
        states={QX_TP: [MessageHandler(NUM_FILTER, qx_tp)], QX_SL: [MessageHandler(NUM_FILTER, qx_sl)],
                QX_BAL: [MessageHandler(NUM_FILTER, qx_bal)]}, fallbacks=fallbacks))
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Text([B2]), mn_start)],
        states={MN_TP: [MessageHandler(NUM_FILTER, mn_tp)], MN_BAL: [MessageHandler(NUM_FILTER, mn_bal)]},
        fallbacks=fallbacks))
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Text([B11]), bt_start)],
        states={BT_PAIR: [MessageHandler(filters.Text(['EUR/USD', 'GBP/USD', 'AUD/USD', 'USD/JPY', 'EUR/GBP', 'USD/CAD']), bt_pair)]},
        fallbacks=fallbacks))

    app.add_handler(CommandHandler('start', start_cmd))
    app.add_handler(CommandHandler('amount', cmd_amount))
    app.add_handler(CommandHandler('payout', cmd_payout))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, on_audio))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))
    log.info('Bot polling shuru hocche...')
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
