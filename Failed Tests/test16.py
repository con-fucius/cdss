import yfinance as yf
import pandas as pd
import numpy as np
from datetime import time, datetime

# 1. Download 15m Gold Data (Last 60 days - yfinance limit for 15m)
print("Downloading XAUUSD 15m data...")
df = yf.download("GC=F", interval="15m", period="60d")
df = df.dropna()

if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

# Ensure timezone is GMT, then strip tz so .date keys match the tz-aware index on join
if df.index.tz is None:
    df.index = df.index.tz_localize('GMT')
df.index = df.index.tz_convert('GMT').tz_localize(None)

df['Time'] = df.index.time
df['Date'] = df.index.date

# 2. Calculate Asian Session High/Low (23:00 to 06:00 GMT)
asian_session = df[(df['Time'] >= time(23, 0)) | (df['Time'] <= time(6, 0))]
asian_ranges = asian_session.groupby('Date').agg(Asian_High=('High', 'max'), Asian_Low=('Low', 'min'))

# Join and shift so London/NY uses the *previous* night's Asian range
df = df.join(asian_ranges)
df['Asian_High'] = df['Asian_High'].shift(1)
df['Asian_Low'] = df['Asian_Low'].shift(1)

# 3. Identify London (07:00-10:00) and NY (12:00-15:00) Execution Windows
df['TradeWindow'] = df['Time'].apply(lambda t: (time(7, 0) <= t <= time(10, 0)) or (time(12, 0) <= t <= time(15, 0)))

# 4. Trend Bias (Price > 50-period SMA)
df['Trend'] = df['Close'] > df['Close'].rolling(50).mean()

print(f"Total 15m candles loaded: {len(df)}")
print(f"Trade windows found: {df['TradeWindow'].sum()}")

# 5. The Sweep & Reclaim Logic (Improved)
trades = []
in_trade = False
sweep_active_long = False
sweep_active_short = False

entry_price = 0
stop_loss = 0
take_profit = 0

for i in range(len(df)):
    row = df.iloc[i]
    
    # --- MANAGE OPEN TRADES ---
    if in_trade:
        if row['Low'] <= stop_loss:
            trades.append({'Date': row.name, 'Type': 'SL', 'PnL': stop_loss - entry_price})
            in_trade = False
            sweep_active_long = False
            sweep_active_short = False
        elif row['High'] >= take_profit:
            trades.append({'Date': row.name, 'Type': 'TP', 'PnL': take_profit - entry_price})
            in_trade = False
            sweep_active_long = False
            sweep_active_short = False
        elif row['Time'] == time(20, 0): # Time Stop
            trades.append({'Date': row.name, 'Type': 'Time', 'PnL': row['Close'] - entry_price})
            in_trade = False
            sweep_active_long = False
            sweep_active_short = False
            
    # --- LOOK FOR NEW ENTRIES ---
    # Reset sweeps if we leave the trade window or it's a new day
    if not row['TradeWindow']:
        sweep_active_long = False
        sweep_active_short = False
        continue
        
    if pd.isna(row['Asian_Low']) or not row['Trend']:
        continue

    # Arm the long sweep if price drops below Asian Low
    if row['Low'] < row['Asian_Low']:
        sweep_active_long = True
        
    # Arm the short sweep if price goes above Asian High
    if row['High'] > row['Asian_High']:
        sweep_active_short = True

    # Trigger Long Entry: Sweep was armed, and current candle closes back above Asian Low
    if not in_trade and sweep_active_long and row['Close'] > row['Asian_Low']:
        entry_price = row['Close']
        # Stop loss is $1 below the lowest low of the last 3 candles (including the sweep)
        lookback_low = df.iloc[max(0, i-3):i+1]['Low'].min()
        stop_loss = lookback_low - 1.00
        take_profit = row['Asian_High']
        
        risk = entry_price - stop_loss
        reward = take_profit - entry_price
        
        # Ensure minimum 1:1 Risk Reward
        if risk > 0 and reward >= risk:
            in_trade = True
            sweep_active_long = False
            
    # Trigger Short Entry: Sweep was armed, and current candle closes back below Asian High
    elif not in_trade and sweep_active_short and row['Close'] < row['Asian_High']:
        entry_price = row['Close']
        lookback_high = df.iloc[max(0, i-3):i+1]['High'].max()
        stop_loss = lookback_high + 1.00
        take_profit = row['Asian_Low']
        
        risk = stop_loss - entry_price
        reward = entry_price - take_profit
        
        if risk > 0 and reward >= risk:
            in_trade = True
            sweep_active_short = False

# 6. Results Analysis
if trades:
    trades_df = pd.DataFrame(trades)
    # Apply $1.00 friction per trade (to simulate spread/commission on 15m)
    trades_df['Net_PnL'] = trades_df['PnL'] - 1.00 
    
    wins = trades_df[trades_df['Net_PnL'] > 0]
    losses = trades_df[trades_df['Net_PnL'] <= 0]
    
    win_rate = len(wins) / len(trades_df) * 100
    avg_win = wins['Net_PnL'].mean()
    avg_loss = abs(losses['Net_PnL'].mean())
    profit_factor = (wins['Net_PnL'].sum()) / (abs(losses['Net_PnL'].sum()) + 0.0001)
    
    print("\n--- BACKTEST RESULTS (60-Day Sample) ---")
    print(f"Total Trades: {len(trades_df)}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Avg Win: ${avg_win:.2f}")
    print(f"Avg Loss: ${avg_loss:.2f}")
    print(f"Profit Factor: {profit_factor:.2f}")
else:
    print("Still no trades generated. The 60-day yfinance sample might lack the volatility needed, or we need to loosen the 1:1 R:R filter.")