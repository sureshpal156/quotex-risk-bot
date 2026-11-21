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


# ---------------- PROFIT / LOSS DATA ----------------
def now_ist():
    return datetime.datetime.now(TIMEZONE)


def get_stats():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_stats(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def day_record(data, day):
    rec = data.get(day)
    if not isinstance(rec, dict):
        rec = {'wins': 0, 'losses': 0, 'profit': 0.0, 'history': []}
        data[day] = rec
    rec.setdefault('wins', 0)
    rec.setdefault('losses', 0)
    rec.setdefault('profit', 0.0)
    rec.setdefault('history', [])
    return rec


def update_trade_data(result, pnl):
    data = get_stats()
    today = now_ist().strftime('%Y-%m-%d')
    rec = day_record(data, today)
    if result == 'WIN':
        rec['wins'] += 1
    else:
        rec['losses'] += 1
    rec['profit'] = round(rec['profit'] + pnl, 2)
    rec['history'].append({'time': now_ist().strftime('%H:%M:%S'), 'result': result, 'pnl': round(pnl, 2)})
    save_stats(data)
    return rec


def today_rec():
    data = get_stats()
    return day_record(data, now_ist().strftime('%Y-%m-%d'))


def stats_text():
    data = get_stats()
    rec = today_rec()
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
        f'🟢 Win: {rec["wins"]}  🔴 Loss: {rec["losses"]}',
        f'🎯 Win Rate: {wr:.0f}%',
        f'💵 Net: ${rec["profit"]:.2f}',
        '',
        '*শেষ ৭ দিন:*',
        f'🟢 {week_w} Win | 🔴 {week_l} Loss | 💵 Net ${week_net:.2f}',
    ])


# ---------------- MARKET DATA ----------------
PAIR_SYMBOLS = {
    'EUR/USD': 'EURUSD=X', 'GBP/USD': 'GBPUSD=X', 'AUD/USD': 'AUDUSD=X',
    'USD/JPY': 'USDJPY=X', 'EUR/GBP': 'EURGBP=X', 'USD/CAD': 'USDCAD=X',
}


def fetch_1m(symbol):
    return yf.Ticker(symbol).history(period='1d', interval='1m')


def analyze_market_backtest(pair):
    symbol = PAIR_SYMBOLS.get(pair, 'EURUSD=X')
    df = fetch_1m(symbol)
    if df is None or df.empty or len(df) < 30:
        return None, df
    price = float(df['Close'].iloc[-1])
    if 'JPY' in pair:
        base = round(price, 0)
        step = 0.5
    else:
        base = round(price, 2)
        step = 0.0025
    rounds = [base + step * k for k in (-2, -1, 0, 1, 2)]
    return {'price': price, 'support': float(df['Low'].min()), 'resistance': float(df['High'].max()),
            'rounds': rounds, 'decimals': 3 if 'JPY' in pair else 5}, df


def make_chart(df, pair):
    try:
        import mplfinance as mpf
        path = os.path.join(tempfile.gettempdir(), 'chart_' + now_ist().strftime('%H%M%S') + '.png')
        mc = mpf.make_marketcolors(up='#00E676', down='#FF5252', edge='inherit', wick='inherit')
        style = mpf.make_mpf_style(marketcolors=mc, base_mpf_style='nightclouds', gridstyle=':')
        mpf.plot(df.tail(30), type='candle', style=style, title=pair + ' (1 min)',
                 savefig=dict(fname=path, dpi=130, bbox_inches='tight'), volume=False)
        return path
    except Exception as e:
        log.warning('chart error: %s', e)
        return None


def calm_report():
    df = fetch_1m('EURUSD=X')
    if df is None or df.empty or len(df) < 15:
        return None
    last = df.tail(15)
    pips = (float(last['High'].max()) - float(last['Low'].min())) * 10000
    return pips


# ---------------- UI ----------------
B1 = '1. Compound'
B2 = '2. Monthly Calculator'
B3 = '3. News & Decision'
B4 = '4. Unlimited Music & Vibe'
B5 = '5. 📊 Live Charts Console'
B6 = '6. ⚡ Auto Best Time Finder'
B7 = '7. 📌 Permanent Trading Bar'
B8 = '8. 📈 Profit/Loss Stats'
B9 = '9 & 10. Psychology & Reality Check'
B11 = '11. 🎯 Real Market Backtest'
B12 = '12. 🤖 Bot Control'
B13 = '13. Risk Management & Settings'
BACK = '🔙 Back to Menu'
MENU_LABELS = [B1, B2, B3, B4, B5, B6, B7, B8, B9, B11, B12, B13, BACK]


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
    return NL.join([
        '┌──────────────────────────┐',
        f'│ 📅 {now.strftime("%Y-%m-%d"):<21} │',
        f'│ ⏰ {now.strftime("%I:%M:%S %p IST"):<21} │',
        f'│ 🌐 {session:<21} │',
        '└──────────────────────────┘',
    ]) + NL + NL


def main_keyboard():
    rows = [[B1, B2], [B3, B4], [B5, B6], [B7, B8], [B9, B11], [B12, B13]]
    return ReplyKeyboardMarkup([[KeyboardButton(x) for x in r] for r in rows], resize_keyboard=True)


def pair_keyboard():
    rows = [['EUR/USD', 'GBP/USD'], ['AUD/USD', 'USD/JPY'], ['EUR/GBP', 'USD/CAD'], [BACK]]
    return ReplyKeyboardMarkup([[KeyboardButton(x) for x in r] for r in rows], resize_keyboard=True)


async def say(update, text, reply_markup=None):
    msg = update.effective_message
    try:
        return await msg.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    except BadRequest:
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


# ---------------- FUNNY ALERTS + VOICE ----------------
WIN_OPEN = [
    'আরে বাহ! ক্যান্ডেল একদম ঠিক জায়গায় গিয়ে গোল দিয়ে দিল',
    'দারুণ ভাই! মার্কেট আজ তোমার জন্যই প্রফিট জমিয়ে রেখেছিল',
    'কী দারুণ এন্ট্রি ছিল! একদম জাদুকরী',
    'সাবাস বস! ক্যান্ডেল পুরো তোমার কথা শুনে গ্রিন হয়ে গেল',
    'চমৎকার ট্রেড! হাসিমুখে প্রফিট রিসিভ করো',
    'দারুণ শট! ক্যান্ডেল রকেটের মতো উপরে উঠে গেল',
    'অসাধারণ ভাই! তোমার প্রেডিকশন একদম মিলে গেল',
]
WIN_CLOSE = ['পকেট ভরে গেল!', 'এভাবেই চালিয়ে যাও!', 'কিন্তু নিয়ম মেনে চলো, লোভ কোরো না!',
             'আজকের টার্গেট মাথায় রেখো!', 'ধৈর্য আর নিয়ম, এটাই আসল!']
LOSS_OPEN = [
    'আহা রে! শেষ মুহূর্তে ক্যান্ডেল একটু বাঁকা চাল চালল',
    'ইশ! সামান্যের জন্য মিস হয়ে গেল',
    'মার্কেট একটু দুষ্টুমি করল আর কি',
    'মন খারাপ করো না ভাই, এটা একটা ছোট স্পিডব্রেকার',
    'কোনো ব্যাপার না, এমন হয়েই থাকে',
    'ক্যান্ডেলটা আজ একটু বেশিই পাজিমি করছে',
]
LOSS_CLOSE = ['মাথা ঠান্ডা রাখো, পরের এন্ট্রি নিয়ম মেনে নাও।', 'রিভেঞ্জ ট্রেড কোরো না, একটু বিরতি নাও।',
              'স্টপলস মনে রেখো, লস তাড়াহুড়ো করে তুলতে যেও না।', 'চা খেয়ে মুড ফ্রেশ করো।']


def get_alert(result):
    if result == 'WIN':
        return random.choice(WIN_OPEN) + '! ' + random.choice(WIN_CLOSE)
    return random.choice(LOSS_OPEN) + '! ' + random.choice(LOSS_CLOSE)


def make_voice_file(text):
    path = os.path.join(tempfile.gettempdir(), 'voice_' + str(random.randint(1000, 999999)) + '.mp3')
    gTTS(text=text, lang='bn', slow=False).save(path)
    return path


async def send_result_alert(context, chat_id, result):
    text = get_alert(result)
    icon = '🚀💰' if result == 'WIN' else '☕🧊'
    await context.bot.send_message(chat_id=chat_id, text=text + ' ' + icon)
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


# ---------------- TRADING BAR ----------------
active_chats = set()


def bar_view(context):
    rec = today_rec()
    pair = context.user_data.get('pair', 'EUR/USD')
    amount = context.user_data.get('amount', 2.0)
    text = NL.join([
        make_box() + '📌 *[LIVE TRADING BAR]*',
        f'🎯 Pair: {pair}  |  💲 Stake: ${amount:.2f}',
        f'🟢 Win: {rec["wins"]} | 🔴 Loss: {rec["losses"]} | 💵 Net: ${rec["profit"]:.2f}',
        '-----------------------------------',
        '⚡ এন্ট্রি নিয়ে বাটন চাপো, তারপর রেজাল্ট দাও:',
    ])
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('🟢 UP (CALL)', callback_data='bar_up'),
         InlineKeyboardButton('🔴 DOWN (PUT)', callback_data='bar_down')],
        [InlineKeyboardButton('📊 Stats', callback_data='bar_stats'),
         InlineKeyboardButton('🛑 STOP', callback_data='bar_stop')],
    ])
    return text, kb


async def open_bar(update, context):
    active_chats.add(update.effective_chat.id)
    text, kb = bar_view(context)
    await say(update, text, kb)


async def on_callback(update, context):
    q = update.callback_query
    data = q.data
    chat_id = q.message.chat_id
    if data in ('bar_up', 'bar_down'):
        direction = 'UP' if data == 'bar_up' else 'DOWN'
        await q.answer()
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('✅ WIN', callback_data='res_win'),
             InlineKeyboardButton('❌ LOSS', callback_data='res_loss')],
            [InlineKeyboardButton('↩️ Cancel', callback_data='res_cancel')],
        ])
        await safe_edit(q, '📌 ' + direction + ' এন্ট্রির রেজাল্ট কী হলো?', kb)
    elif data in ('res_win', 'res_loss'):
        await q.answer()
        result = 'WIN' if data == 'res_win' else 'LOSS'
        amount = context.user_data.get('amount', 2.0)
        payout = context.user_data.get('payout', 85.0)
        pnl = amount * payout / 100 if result == 'WIN' else -amount
        update_trade_data(result, pnl)
        text, kb = bar_view(context)
        await safe_edit(q, text, kb)
        await send_result_alert(context, chat_id, result)
    elif data == 'res_cancel':
        await q.answer()
        text, kb = bar_view(context)
        await safe_edit(q, text, kb)
    elif data == 'bar_stats':
        rec = today_rec()
        await q.answer('Win ' + str(rec['wins']) + ' | Loss ' + str(rec['losses']) + ' | Net $' + format(rec['profit'], '.2f'), show_alert=True)
    elif data == 'bar_stop':
        await q.answer()
        active_chats.discard(chat_id)
        await safe_edit(q, '🛑 Trading bar বন্ধ। আবার চালাতে মেনু থেকে ' + B7 + ' চাপো।')
    elif data == 'ctl_on':
        active_chats.add(chat_id)
        await q.answer('✅ Volatility alert চালু')
    elif data == 'ctl_off':
        active_chats.discard(chat_id)
        await q.answer('⏸ Volatility alert বন্ধ')
    elif data == 'ctl_bar':
        await q.answer()
        active_chats.add(chat_id)
        text, kb = bar_view(context)
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=kb, parse_mode='Markdown')
    else:
        await q.answer()


# ---------------- SIMPLE MENU ITEMS ----------------
async def show_stats(update, context):
    await say(update, stats_text())


async def show_news(update, context):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('📅 Forex Factory Calendar', url='https://www.forexfactory.com/calendar')],
        [InlineKeyboardButton('📰 Investing.com Calendar', url='https://www.investing.com/economic-calendar/')],
    ])
    text = NL.join([
        '📰 *News & Decision*',
        '',
        'আমি লাইভ নিউজ বানিয়ে বলি না। নিচের ক্যালেন্ডারে red folder (বড় নিউজ) দেখে নাও।',
        '',
        '*সিদ্ধান্তের চেকলিস্ট:*',
        '1. বড় নিউজের ৩০ মিনিট আগে ও পরে ট্রেড নয়',
        '2. আজকের স্টপলস ছোঁয়া হয়ে গেলে আর ট্রেড নয়',
        '3. সিগন্যাল পরিষ্কার না হলে ট্রেড নয়',
        '4. প্রতি ট্রেডে ব্যালেন্সের ১-২% এর বেশি নয়',
    ])
    await say(update, text, kb)


VIBES = [
    'ধৈর্য ধরো, নিয়ম মেনে চলো, প্রফিট নিজেই আসবে। 💪',
    'একটা লস মানে সব শেষ না, রিস্ক ম্যানেজমেন্টই তোমাকে বাঁচিয়ে রাখবে। 🧠',
    'প্রতিদিন একটু একটু করে এগোলে ২০৩০ এ নিজের পায়ে দাঁড়ানো সম্ভব। 🚀',
    'লোভ কমাও, টার্গেট পূরণ হলে থেমে যাও। 🎯',
]


async def show_music(update, context):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('🎵 Hindi Motivational Songs', url='https://www.youtube.com/results?search_query=hindi+motivational+songs')],
        [InlineKeyboardButton('🎧 Relax / Lofi', url='https://www.youtube.com/results?search_query=lofi+relax+music')],
    ])
    await say(update, '🎶 *Music & Vibe*' + NL + NL + random.choice(VIBES), kb)


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
    await say(update, '⏳ মার্কেট চেক করছি...')
    try:
        pips = await asyncio.to_thread(calm_report)
    except Exception:
        pips = None
    if pips is None:
        mood = 'এখন লাইভ ডেটা পাওয়া যাচ্ছে না (মার্কেট বন্ধ থাকতে পারে, যেমন উইকএন্ডে)।'
    else:
        if pips < 3:
            mood = 'খুব শান্ত, মুভমেন্ট কম।'
        elif pips < 10:
            mood = 'মোটামুটি স্বাভাবিক মুভমেন্ট।'
        else:
            mood = 'বেশ অস্থির, সাবধানে।'
        mood = 'EUR/USD গত ১৫ মিনিটে প্রায় ' + format(pips, '.1f') + ' pips নড়েছে। ' + mood
    text = NL.join([
        '⚡ *Best Time Finder*',
        '',
        mood,
        '',
        'সাধারণত London ও New York overlap (প্রায় ১৮:৩০ থেকে ২২:৩০ IST) সময়ে মুভমেন্ট বেশি থাকে। এটা আন্দাজ, গ্যারান্টি না।',
    ])
    await say(update, text)


async def show_psychology(update, context):
    text = NL.join([
        '🧠 *Psychology & Reality Check*',
        '',
        '*মাথায় রাখো:*',
        '- বেশিরভাগ বাইনারি ট্রেডার লস করে, এটা বাস্তব।',
        '- লস হলে সঙ্গে সঙ্গে রিভেঞ্জ ট্রেড নয়।',
        '- টার্গেট পূরণ হলে থেমে যাও।',
        '- যে টাকা হারালে সংসার চলবে না, ওটা দিয়ে ট্রেড নয়।',
        '- ট্রেড একটা কাজ, জুয়া না। নিয়ম লিখে রাখো।',
        '',
        'ট্রেডিংয়ের পাশাপাশি তোমার অন্য কাজও (ই-কমার্স, ভিডিও) চালিয়ে যাও।',
    ])
    await say(update, text)


async def show_control(update, context):
    on = update.effective_chat.id in active_chats
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('▶️ Alert ON', callback_data='ctl_on'),
         InlineKeyboardButton('⏸ Alert OFF', callback_data='ctl_off')],
        [InlineKeyboardButton('📌 Trading Bar খোলো', callback_data='ctl_bar')],
    ])
    status = 'চালু ✅' if on else 'বন্ধ ⏸'
    await say(update, '🤖 *Bot Control*' + NL + NL + 'Volatility alert এখন: ' + status, kb)


async def show_risk(update, context):
    amount = context.user_data.get('amount', 2.0)
    payout = context.user_data.get('payout', 85.0)
    be = 100 / (100 + payout) * 100
    text = NL.join([
        '🛡 *Risk Management & Settings*',
        '',
        '- প্রতি ট্রেডে ব্যালেন্সের ১-২% এর বেশি নয়',
        '- দৈনিক স্টপলস (যেমন ৩-৫%) ছুঁলে ওই দিন বন্ধ',
        '- ব্রেক-ইভেন Win Rate: payout ' + format(payout, '.0f') + '% হলে প্রায় ' + format(be, '.1f') + '%',
        '',
        '*এখনকার সেটিং:* Stake $' + format(amount, '.2f') + ' | Payout ' + format(payout, '.0f') + '%',
        '',
        'বদলাতে লেখো:',
        '/amount 5  (Stake)',
        '/payout 85  (Payout %)',
    ])
    await say(update, text)


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
        await say(update, 'ব্যবহার: /amount 5')
        return
    context.user_data['amount'] = val
    await say(update, '✅ Stake সেট: $' + format(val, '.2f'))


async def cmd_payout(update, context):
    val = parse_num(context.args[0]) if context.args else None
    if val is None or not (1 <= val <= 100):
        await say(update, 'ব্যবহার: /payout 85')
        return
    context.user_data['payout'] = val
    await say(update, '✅ Payout সেট: ' + format(val, '.0f') + '%')


# ---------------- CALCULATOR CONVERSATIONS ----------------
(C_TARGET, C_SL, C_BAL, C_PAYOUT, M_PROFIT, M_SL, M_BAL, BT_PAIR) = range(8)
NUM_FILTER = filters.TEXT & ~filters.COMMAND & ~filters.Text(MENU_LABELS)


async def start_compound(update, context):
    await say(update, make_box() + '🧮 *Compound Calculator*' + NL + NL + '১. দৈনিক প্রফিট টার্গেট কত %? (যেমন 5):')
    return C_TARGET


async def c_target(update, context):
    v = parse_num(update.message.text)
    if v is None or v <= 0:
        await say(update, '❌ সঠিক সংখ্যা লেখো (যেমন 5):')
        return C_TARGET
    context.user_data['c'] = {'target': v}
    await say(update, '২. দৈনিক স্টপলস কত %? (যেমন 3):')
    return C_SL


async def c_sl(update, context):
    v = parse_num(update.message.text)
    if v is None or v <= 0:
        await say(update, '❌ সঠিক সংখ্যা লেখো (যেমন 3):')
        return C_SL
    context.user_data['c']['sl'] = v
    await say(update, '৩. তোমার ব্যালেন্স কত $? (যেমন 100):')
    return C_BAL


async def c_bal(update, context):
    v = parse_num(update.message.text)
    if v is None or v <= 0:
        await say(update, '❌ সঠিক সংখ্যা লেখো (যেমন 100):')
        return C_BAL
    context.user_data['c']['bal'] = v
    await say(update, '৪. Payout কত %? (যেমন 85):')
    return C_PAYOUT


async def c_payout(update, context):
    v = parse_num(update.message.text)
    if v is None or not (1 <= v <= 100):
        await say(update, '❌ ১ থেকে ১০০ এর মধ্যে লেখো (যেমন 85):')
        return C_PAYOUT
    c = context.user_data['c']
    bal, tgt, sl = c['bal'], c['target'], c['sl']
    target_amt = bal * tgt / 100
    sl_amt = bal * sl / 100
    stake = round(bal * 0.02, 2)
    win_profit = stake * v / 100
    wins_needed = math.ceil(target_amt / win_profit) if win_profit > 0 else 0
    max_losses = int(sl_amt // stake) if stake > 0 else 0
    be = 100 / (100 + v) * 100
    proj = []
    for n in (7, 15, 30):
        proj.append('- ' + str(n) + ' দিন: $' + format(bal * (1 + tgt / 100) ** n, '.2f'))
    text = NL.join([
        '🧮 *Compound Result*',
        '',
        '🎯 দৈনিক টার্গেট: $' + format(target_amt, '.2f') + ' (' + format(tgt, 'g') + '%)',
        '🛑 দৈনিক স্টপলস: $' + format(sl_amt, '.2f') + ' (' + format(sl, 'g') + '%)',
        '💲 প্রস্তাবিত Stake (ব্যালেন্সের ২%): $' + format(stake, '.2f'),
        '✅ টার্গেটে পৌঁছাতে লাগবে প্রায় ' + str(wins_needed) + ' টা Win',
        '❌ স্টপলস ছুঁতে ' + str(max_losses) + ' টা Loss যথেষ্ট',
        '📊 ব্রেক-ইভেন Win Rate: ' + format(be, '.1f') + '%',
        '',
        '*যদি প্রতিদিন টার্গেট পূরণ হয় (শুধু হিসাব):*',
    ] + proj + [
        '',
        '⚠️ বাস্তবে প্রতিদিন টার্গেট পূরণ হয় না। এটা শুধু অঙ্কের হিসাব, লাভের গ্যারান্টি না।',
    ])
    await say(update, text, main_keyboard())
    return ConversationHandler.END


async def start_monthly(update, context):
    await say(update, make_box() + '📅 *Monthly Calculator*' + NL + NL + '১. মাসিক প্রফিট টার্গেট কত %? (যেমন 20):')
    return M_PROFIT


async def m_profit(update, context):
    v = parse_num(update.message.text)
    if v is None or v <= 0:
        await say(update, '❌ সঠিক সংখ্যা লেখো (যেমন 20):')
        return M_PROFIT
    context.user_data['m'] = {'profit': v}
    await say(update, '২. মাসিক স্টপলস কত %? (যেমন 10):')
    return M_SL


async def m_sl(update, context):
    v = parse_num(update.message.text)
    if v is None or v <= 0:
        await say(update, '❌ সঠিক সংখ্যা লেখো (যেমন 10):')
        return M_SL
    context.user_data['m']['sl'] = v
    await say(update, '৩. তোমার ব্যালেন্স কত $? (যেমন 100):')
    return M_BAL


async def m_bal(update, context):
    v = parse_num(update.message.text)
    if v is None or v <= 0:
        await say(update, '❌ সঠিক সংখ্যা লেখো (যেমন 100):')
        return M_BAL
    m = context.user_data['m']
    target_amt = v * m['profit'] / 100
    sl_amt = v * m['sl'] / 100
    days = 22
    text = NL.join([
        '📅 *Monthly Result*',
        '',
        '🎯 মাসিক টার্গেট: $' + format(target_amt, '.2f') + ' (' + format(m['profit'], 'g') + '%)',
        '🛑 মাসিক স্টপলস: $' + format(sl_amt, '.2f') + ' (' + format(m['sl'], 'g') + '%)',
        '',
        '(মাসে ২২ ট্রেডিং দিন ধরে)',
        '- দৈনিক গড় টার্গেট: $' + format(target_amt / days, '.2f') + ' (ব্যালেন্সের ' + format(m['profit'] / days, '.2f') + '%)',
        '- সাপ্তাহিক গড় টার্গেট: $' + format(target_amt / 4, '.2f'),
        '',
        '⚠️ এটা শুধু পরিকল্পনার হিসাব, লাভের গ্যারান্টি না।',
    ])
    await say(update, text, main_keyboard())
    return ConversationHandler.END


async def start_backtest(update, context):
    await say(update, make_box() + '🎯 *Real Market Backtest*' + NL + NL + 'Pair বাছাই করো:', pair_keyboard())
    return BT_PAIR


async def bt_pair(update, context):
    pair = update.message.text.strip()
    await say(update, '⏳ ' + pair + ' এর ডেটা আনছি...')
    try:
        res, df = await asyncio.to_thread(analyze_market_backtest, pair)
    except Exception as e:
        log.warning('backtest error: %s', e)
        res, df = None, None
    if res is None:
        await say(update, '❌ পর্যাপ্ত মার্কেট ডেটা পাওয়া যায়নি (মার্কেট বন্ধ থাকতে পারে, যেমন উইকএন্ডে)।', main_keyboard())
        return ConversationHandler.END
    d = res['decimals']
    lines = [
        '🎯 *' + pair + ' Backtest*',
        '',
        '💱 লাইভ দাম: ' + format(res['price'], '.' + str(d) + 'f'),
        '🟢 আজকের Support (লো): ' + format(res['support'], '.' + str(d) + 'f'),
        '🔴 আজকের Resistance (হাই): ' + format(res['resistance'], '.' + str(d) + 'f'),
        '',
        '*কাছাকাছি Round Number:*',
    ]
    for r in res['rounds']:
        lines.append('- ' + format(r, '.' + str(d) + 'f'))
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
MENU_ACTIONS = {B3: show_news, B4: show_music, B5: show_charts, B6: show_best_time, B7: open_bar,
                B8: show_stats, B9: show_psychology, B12: show_control, B13: show_risk}


async def menu_router(update, context):
    text = (update.message.text or '').strip()
    fn = MENU_ACTIONS.get(text)
    if fn:
        await fn(update, context)
    elif text == BACK:
        await send_menu(update, context)
    else:
        await say(update, 'মেনু থেকে একটা বাটন চাপো 👇', main_keyboard())


async def start_cmd(update, context):
    await send_menu(update, context)


# ---------------- BACKGROUND VOLATILITY WATCHER ----------------
async def market_watcher(application):
    while True:
        await asyncio.sleep(600)
        try:
            if not active_chats:
                continue
            df = await asyncio.to_thread(fetch_1m, 'EURUSD=X')
            if df is not None and len(df) >= 10:
                std = float(df['Close'].tail(10).std())
                if std > 0.00030:
                    for chat_id in list(active_chats):
                        await application.bot.send_message(
                            chat_id=chat_id,
                            text='🚨 WARNING: মার্কেট হঠাৎ খুব অস্থির! বড় মুভমেন্ট চলছে, সাবধানে থাকো।')
        except Exception as e:
            log.warning('watcher error: %s', e)


async def post_init(application):
    application.bot_data['watcher'] = asyncio.create_task(market_watcher(application))


def main():
    if not BOT_TOKEN:
        raise SystemExit('BOT_TOKEN environment variable set kora nei')
    keep_alive()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(TypeHandler(Update, gate), group=-1)

    fallbacks = [CommandHandler('cancel', cancel), MessageHandler(filters.Text(MENU_LABELS), cancel)]
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Text([B1]), start_compound)],
        states={C_TARGET: [MessageHandler(NUM_FILTER, c_target)], C_SL: [MessageHandler(NUM_FILTER, c_sl)],
                C_BAL: [MessageHandler(NUM_FILTER, c_bal)], C_PAYOUT: [MessageHandler(NUM_FILTER, c_payout)]},
        fallbacks=fallbacks))
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Text([B2]), start_monthly)],
        states={M_PROFIT: [MessageHandler(NUM_FILTER, m_profit)], M_SL: [MessageHandler(NUM_FILTER, m_sl)],
                M_BAL: [MessageHandler(NUM_FILTER, m_bal)]},
        fallbacks=fallbacks))
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Text([B11]), start_backtest)],
        states={BT_PAIR: [MessageHandler(filters.Text(list(PAIR_SYMBOLS.keys())), bt_pair)]},
        fallbacks=fallbacks))

    app.add_handler(CommandHandler('start', start_cmd))
    app.add_handler(CommandHandler('amount', cmd_amount))
    app.add_handler(CommandHandler('payout', cmd_payout))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))
    log.info('Bot polling shuru hocche...')
    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
