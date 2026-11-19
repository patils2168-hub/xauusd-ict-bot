import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ============================================================================
# 1. CONFIGURATION (Matches Your Winning 15M Strategy)
# ============================================================================
TWELVE_DATA_API_KEY = (os.getenv("TWELVE_DATA_API_KEY") or "").strip()
TELEGRAM_BOT_TOKEN  = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID    = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
SEND_TEST_MSG       = os.getenv("SEND_TEST_MSG", "false").lower() == "true"

SYMBOL        = "XAU/USD"
INTERVAL      = "15min"
MAJOR_LEN     = 5
MINOR_LEN     = 3
POI_EXPIRY    = 60
RR_TARGET1    = 1.5
RR_TARGET2    = 2.0
SL_BUFFER     = 0.3
LOT_SIZE      = 0.01  # Starting lot size

IST = timezone(timedelta(hours=5, minutes=30))

# ============================================================================
# 2. TELEGRAM SENDER
# ============================================================================
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    r = requests.post(url, json=payload, timeout=15)
    r.raise_for_status()
    print("Telegram alert sent successfully.")

# ============================================================================
# 3. FETCH 15M XAUUSD DATA FROM TWELVE DATA
# ============================================================================
def fetch_candles() -> pd.DataFrame:
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": 300,
        "timezone": "UTC",
        "apikey": TWELVE_DATA_API_KEY
    }
    r = requests.get(url, params=params, timeout=20)
    data = r.json()

    if "values" not in data:
        raise RuntimeError(f"Twelve Data API error: {data}")

    df = pd.DataFrame(data["values"])
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)

    # Sort oldest to newest
    df = df.sort_values("datetime").reset_index(drop=True)

    # Drop the last candle if it is still forming (not yet closed)
    now_utc = datetime.now(timezone.utc)
    if (now_utc - df.iloc[-1]["datetime"].to_pydatetime()) < timedelta(minutes=15):
        df = df.iloc[:-1].reset_index(drop=True)

    # Wilder's ATR(14) matching TradingView ta.atr(14)
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1/14, adjust=False).mean()

    return df

# ============================================================================
# 4. HELPER FUNCTIONS
# ============================================================================
def is_in_killzone(dt_utc: pd.Timestamp) -> bool:
    dt_ist = dt_utc.tz_convert(IST)
    t = dt_ist.hour * 60 + dt_ist.minute
    in_london = (12 * 60 + 30) <= t <= (15 * 60 + 30)  # 12:30 - 15:30 IST
    in_ny     = (17 * 60 + 30) <= t <= (20 * 60 + 30)  # 17:30 - 20:30 IST
    return in_london or in_ny

def find_last_opposite_candle(df: pd.DataFrame, pivot_idx: int, want_up: bool):
    start = max(0, pivot_idx - 2)
    end   = min(len(df) - 1, pivot_idx + 2)
    for idx in range(end, start - 1, -1):
        row = df.iloc[idx]
        if want_up and row["close"] > row["open"]:
            return row["high"], row["low"]
        if not want_up and row["close"] < row["open"]:
            return row["high"], row["low"]
    row = df.iloc[pivot_idx]
    return row["high"], row["low"]

# ============================================================================
# 5. ICT 4-CONCEPT STRATEGY ENGINE
# ============================================================================
def run_strategy(df: pd.DataFrame):
    mSH1 = mSH2 = mSH1_idx = None
    mSL1 = mSL2 = mSL1_idx = None
    maj_bear_bos_done = False
    maj_bull_bos_done = False

    iSH1 = iSL1 = None

    active_bear_poi = False
    bear_poi_top = bear_poi_bot = bear_mb_high = bear_poi_idx = None
    bear_tapped = False

    active_bull_poi = False
    bull_poi_top = bull_poi_bot = bull_mb_low = bull_poi_idx = None
    bull_tapped = False

    latest_signal = None
    last_bar_idx = len(df) - 1

    highs  = df["high"].values
    lows   = df["low"].values
    opens  = df["open"].values
    closes = df["close"].values
    atrs   = df["atr"].values

    for i in range(len(df)):
        # --- Confirm Major Pivots (non-repainting, delayed by MAJOR_LEN) ---
        if i >= 2 * MAJOR_LEN:
            p = i - MAJOR_LEN
            window_h = highs[p - MAJOR_LEN : p + MAJOR_LEN + 1]
            window_l = lows[p - MAJOR_LEN : p + MAJOR_LEN + 1]
            if highs[p] == np.max(window_h):
                mSH2, mSH1, mSH1_idx = mSH1, highs[p], p
                maj_bear_bos_done = False
            if lows[p] == np.min(window_l):
                mSL2, mSL1, mSL1_idx = mSL1, lows[p], p
                maj_bull_bos_done = False

        # --- Confirm Minor Pivots ---
        if i >= 2 * MINOR_LEN:
            p = i - MINOR_LEN
            if highs[p] == np.max(highs[p - MINOR_LEN : p + MINOR_LEN + 1]):
                iSH1 = highs[p]
            if lows[p] == np.min(lows[p - MINOR_LEN : p + MINOR_LEN + 1]):
                iSL1 = lows[p]

        # --- A. Bearish Mitigation Block + FVG ---
        if (mSH1 is not None and mSH2 is not None and mSL1 is not None
                and mSH1 < mSH2 and mSH1_idx > mSL1_idx
                and not maj_bear_bos_done and closes[i] < mSL1):
            maj_bear_bos_done = True
            mb_top, mb_bot = find_last_opposite_candle(df, mSH1_idx, want_up=True)
            leg_bars = min(i - mSH1_idx, 80)
            for k in range(0, leg_bars + 1):
                c_idx = i - k
                if c_idx >= 2:
                    fvg_top = lows[c_idx - 2]
                    fvg_bot = highs[c_idx]
                    if fvg_bot < fvg_top:
                        active_bear_poi = True
                        bear_poi_top = max(mb_top, fvg_top)
                        bear_poi_bot = min(mb_bot, fvg_bot)
                        bear_mb_high = mb_top
                        bear_poi_idx = i
                        bear_tapped  = False
                        break

        # --- B. Bullish Mitigation Block + FVG ---
        if (mSL1 is not None and mSL2 is not None and mSH1 is not None
                and mSL1 > mSL2 and mSL1_idx > mSH1_idx
                and not maj_bull_bos_done and closes[i] > mSH1):
            maj_bull_bos_done = True
            mb_top, mb_bot = find_last_opposite_candle(df, mSL1_idx, want_up=False)
            leg_bars = min(i - mSL1_idx, 80)
            for k in range(0, leg_bars + 1):
                c_idx = i - k
                if c_idx >= 2:
                    fvg_top = lows[c_idx]
                    fvg_bot = highs[c_idx - 2]
                    if fvg_top > fvg_bot:
                        active_bull_poi = True
                        bull_poi_top = max(mb_top, fvg_top)
                        bull_poi_bot = min(mb_bot, fvg_bot)
                        bull_mb_low  = mb_bot
                        bull_poi_idx = i
                        bull_tapped  = False
                        break

        in_kz = is_in_killzone(df.iloc[i]["datetime"])

        # --- C. Bearish Entry Check ---
        if active_bear_poi:
            if (i - bear_poi_idx > POI_EXPIRY) or (closes[i] > bear_mb_high):
                active_bear_poi = False
                bear_tapped = False
            else:
                if highs[i] >= bear_poi_bot and lows[i] <= bear_poi_top:
                    bear_tapped = True
                bear_prop = closes[i] < opens[i] and (opens[i] - closes[i]) > (atrs[i] * 0.35)
                minor_shift = (iSL1 is not None) and (closes[i] < iSL1)

                if bear_tapped and (bear_prop or minor_shift) and in_kz:
                    active_bear_poi = False
                    bear_tapped = False
                    if i == last_bar_idx:
                        entry = closes[i]
                        sl    = bear_mb_high + (atrs[i] * SL_BUFFER)
                        risk  = max(sl - entry, atrs[i] * 0.5)
                        latest_signal = {
                            "side": "SELL",
                            "time_ist": df.iloc[i]["datetime"].tz_convert(IST).strftime("%d %b %Y, %I:%M %p IST"),
                            "entry": entry,
                            "sl": sl,
                            "tp1": entry - risk * RR_TARGET1,
                            "tp2": entry - risk * RR_TARGET2
                        }

        # --- D. Bullish Entry Check ---
        if active_bull_poi:
            if (i - bull_poi_idx > POI_EXPIRY) or (closes[i] < bull_mb_low):
                active_bull_poi = False
                bull_tapped = False
            else:
                if lows[i] <= bull_poi_top and highs[i] >= bull_poi_bot:
                    bull_tapped = True
                bull_prop = closes[i] > opens[i] and (closes[i] - opens[i]) > (atrs[i] * 0.35)
                minor_shift = (iSH1 is not None) and (closes[i] > iSH1)

                if bull_tapped and (bull_prop or minor_shift) and in_kz:
                    active_bull_poi = False
                    bull_tapped = False
                    if i == last_bar_idx:
                        entry = closes[i]
                        sl    = bull_mb_low - (atrs[i] * SL_BUFFER)
                        risk  = max(entry - sl, atrs[i] * 0.5)
                        latest_signal = {
                            "side": "BUY",
                            "time_ist": df.iloc[i]["datetime"].tz_convert(IST).strftime("%d %b %Y, %I:%M %p IST"),
                            "entry": entry,
                            "sl": sl,
                            "tp1": entry + risk * RR_TARGET1,
                            "tp2": entry + risk * RR_TARGET2
                        }

    return latest_signal

# ============================================================================
# 6. MAIN RUNNER
# ============================================================================
if __name__ == "__main__":
    if SEND_TEST_MSG:
        send_telegram("✅ <b>ICT 4-Concept XAUUSD Bot Connected!</b>\nMonitoring 15M London & NY Killzones.")

    df = fetch_candles()
    signal = run_strategy(df)

    if signal:
        emoji = "🟢" if signal["side"] == "BUY" else "🔴"
        msg = (
            f"{emoji} <b>ICT 4-MODEL {signal['side']} SIGNAL</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Pair:</b> XAUUSD (15M)\n"
            f"🕒 <b>Candle:</b> {signal['time_ist']}\n"
            f"📦 <b>Lot Size:</b> {LOT_SIZE} Lots\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Entry:</b> <code>{signal['entry']:.2f}</code>\n"
            f"🛑 <b>Stop-Loss:</b> <code>{signal['sl']:.2f}</code>\n"
            f"💰 <b>TP1 (1.5R):</b> <code>{signal['tp1']:.2f}</code>\n"
            f"🚀 <b>TP2 (2.0R):</b> <code>{signal['tp2']:.2f}</code>"
        )
        send_telegram(msg)
    else:
        last_time = df.iloc[-1]["datetime"].tz_convert(IST).strftime("%d %b %I:%M %p IST")
        print(f"No signal on latest closed 15m candle ({last_time}).")
