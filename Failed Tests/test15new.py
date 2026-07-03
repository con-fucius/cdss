import yfinance as yf
import pandas as pd
import numpy as np
from datetime import time

# 1. Download 15m Gold Data (Last 60 days for proof of concept - yfinance limits 15m to 60 days)
# Note: For a full 18-year test, you will need to export MT5 history to CSV.
print("Downloading XAUUSD 15m data...")
df = yf.download("GC=F", interval="15m", period="60d")
df = df.dropna()

if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

# Ensure timezone is GMT for session calculations, then strip tz to keep joins consistent
if df.index.tz is not None:
    df.index = df.index.tz_convert('GMT').tz_localize(None)

# 2. Calculate Asian Session High/Low (23:00 to 06:00 GMT)
df['Time'] = df.index.time
df['Date'] = df.index.date

# Filter Asian Session
asian_session = df[(df['Time'] >= time(23, 0)) | (df['Time'] <= time(6, 0))]
asian_ranges = asian_session.groupby('Date').agg(Asian_High=('High', 'max'), Asian_Low=('Low', 'min'))

# Shift by 1 day so we use the *previous* night's Asian range for London/NY trading
df = df.join(asian_ranges)
df['Asian_High'] = df['Asian_High'].shift(1)
df['Asian_Low'] = df['Asian_Low'].shift(1)

# 3. Identify London (07:00-10:00) and NY (12:00-15:00) Execution Windows
df['London'] = df['Time'].apply(lambda t: time(7, 0) <= t <= time(10, 0))
df['NY'] = df['Time'].apply(lambda t: time(12, 0) <= t <= time(15, 0))
df['TradeWindow'] = df['London'] | df['NY']

# 4. 1H Trend Bias (Simplified: Price > 50 Period Moving Average on 15m as proxy)
df['Trend'] = df['Close'] > df['Close'].rolling(50).mean()

# 5. The Sweep & Reclaim Logic
trades = []
in_trade = False
entry_price = 0
stop_loss = 0
take_profit = 0

for i in range(len(df)):
    row = df.iloc[i]
    
    # If in a trade, check for exit
    if in_trade:
        if row['Low'] <= stop_loss:
            trades.append({'Date': row.name, 'Type': 'SL', 'PnL': stop_loss - entry_price})
            in_trade = False
        elif row['High'] >= take_profit:
            trades.append({'Date': row.name, 'Type': 'TP', 'PnL': take_profit - entry_price})
            in_trade = False
        elif row['Time'] == time(20, 0): # Time Stop
            trades.append({'Date': row.name, 'Type': 'Time', 'PnL': row['Close'] - entry_price})
            in_trade = False
            
    # If not in a trade, look for entry
    elif not in_trade and row['TradeWindow'] and row['Trend'] and not pd.isna(row['Asian_Low']):
        # Long Setup: Sweep Asian Low, then close back inside
        if row['Low'] < row['Asian_Low'] and row['Close'] > row['Asian_Low']:
            entry_price = row['Close']
            stop_loss = row['Low'] - 1.00 # $1 below the wick
            take_profit = row['Asian_High']
            
            # Ensure 1:1 minimum RR
            if take_profit > entry_price + (entry_price - stop_loss):
                in_trade = True
                
        # Short Setup: Sweep Asian High, then close back inside
        elif row['High'] > row['Asian_High'] and row['Close'] < row['Asian_High']:
            entry_price = row['Close']
            stop_loss = row['High'] + 1.00
            take_profit = row['Asian_Low']
            
            if take_profit < entry_price - (stop_loss - entry_price):
                in_trade = True

# 6. Results Analysis
if trades:
    trades_df = pd.DataFrame(trades)
    # Apply $0.50 friction per trade (spread + commission)
    trades_df['Net_PnL'] = trades_df['PnL'] - 0.50 
    
    wins = trades_df[trades_df['Net_PnL'] > 0]
    losses = trades_df[trades_df['Net_PnL'] <= 0]
    
    win_rate = len(wins) / len(trades_df) * 100
    avg_win = wins['Net_PnL'].mean()
    avg_loss = abs(losses['Net_PnL'].mean())
    profit_factor = (wins['Net_PnL'].sum()) / (abs(losses['Net_PnL'].sum()))
    
    print("\n--- BACKTEST RESULTS (60-Day Sample) ---")
    print(f"Total Trades: {len(trades_df)}")
    print(f"Win Rate: {win_rate:.2f}%")
    print(f"Avg Win: ${avg_win:.2f}")
    print(f"Avg Loss: ${avg_loss:.2f}")
    print(f"Profit Factor: {profit_factor:.2f}")
else:
    print("No trades generated in this sample period. Check data or session timings.")