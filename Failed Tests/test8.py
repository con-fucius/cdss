import pandas as pd
import numpy as np
import yfinance as yf
import datetime

# ==========================================
# 1. DATA PIPELINE
# ==========================================
end_date = datetime.datetime.now()
start_date = end_date - datetime.timedelta(days=20*365)

print("Fetching Gold data...")
gold = yf.download('GC=F', start=start_date, end=end_date, auto_adjust=True)
gold = gold[['Open', 'High', 'Low', 'Close']]
gold.columns = ['Gold_Open', 'Gold_High', 'Gold_Low', 'Gold_Close']

print("Fetching Macro Proxies (TIP & DXY)...")
tip = yf.download('TIP', start=start_date, end=end_date, auto_adjust=True)[['Close']]
tip.columns = ['TIP_Close']

dxy = yf.download('DX-Y.NYB', start=start_date, end=end_date, auto_adjust=True)[['Close']]
dxy.columns = ['DXY_Close']

# Shift macro by 1 day
tip_shifted = tip.shift(1)
dxy_shifted = dxy.shift(1)

df = gold.join(tip_shifted).join(dxy_shifted).dropna()

# ==========================================
# 2. FEATURE ENGINEERING (V6 - STRUCTURAL)
# ==========================================
# A. Gold Secular Filter: Price > 200-day SMA
df['Gold_SMA_200'] = df['Gold_Close'].rolling(200).mean()
df['Gold_Secular_Bull'] = df['Gold_Close'] > df['Gold_SMA_200']

# B. Macro Structural Filter: TIP > 100 SMA AND DXY < 100 SMA
df['TIP_SMA_100'] = df['TIP_Close'].rolling(100).mean()
df['DXY_SMA_100'] = df['DXY_Close'].rolling(100).mean()

df['Macro_TIP_Structure'] = df['TIP_Close'] > df['TIP_SMA_100']
df['Macro_DXY_Structure'] = df['DXY_Close'] < df['DXY_SMA_100']
df['Macro_Structural_Bull'] = df['Macro_TIP_Structure'] & df['Macro_DXY_Structure']

# C. Trend & Value Zone: 50 EMA
df['EMA_50'] = df['Gold_Close'].ewm(span=50, adjust=False).mean()
df['EMA_200'] = df['Gold_Close'].ewm(span=200, adjust=False).mean()
df['EMA_50_Slope'] = df['EMA_50'] > df['EMA_50'].shift(5)

# D. Setup Condition: Secular Gold Bull + Macro Structure Bull + Trend Up
df['Setup_Active'] = df['Gold_Secular_Bull'] & df['Macro_Structural_Bull'] & (df['EMA_50'] > df['EMA_200']) & df['EMA_50_Slope']

# E. Volatility (ATR)
high_low = df['Gold_High'] - df['Gold_Low']
high_close = np.abs(df['Gold_High'] - df['Gold_Close'].shift())
low_close = np.abs(df['Gold_Low'] - df['Gold_Close'].shift())
ranges = pd.concat([high_low, high_close, low_close], axis=1)
true_range = np.max(ranges, axis=1)
df['ATR_20'] = true_range.rolling(20).mean()

df = df.dropna()

# ==========================================
# 3. BACKTEST ENGINE (V6)
# ==========================================
def run_backtest_v6(df, start_equity, risk_pct, normal_spread_usd):
    
    equity = start_equity
    in_trade = False
    entry_price = 0
    stop_price = 0
    position_size_oz = 0
    highest_close_since_entry = 0
    
    equity_curve = []
    trades = []
    
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i-1]
        
        # --- INJECTION LOGIC ---
        if i == 252: equity += 1000 
            
        # --- MANAGE OPEN TRADES ---
        if in_trade:
            # Swap
            daily_swap_rate = -0.005 / 365
            swap_cost = (position_size_oz * row['Gold_Close']) * daily_swap_rate
            equity -= abs(swap_cost)
            
            # Stop Hit
            if row['Gold_Low'] <= stop_price:
                exit_price = stop_price - normal_spread_usd 
                pnl = (exit_price - entry_price) * position_size_oz
                equity += pnl
                
                trades.append({
                    'Date': row.name, 'Entry': entry_price, 'Exit': exit_price, 
                    'PnL': pnl, 'Equity': equity, 'Era': row.name.year
                })
                in_trade = False

            # Chandelier Trailing Stop
            else:
                highest_close_since_entry = max(highest_close_since_entry, row['Gold_Close'])
                stop_distance = max(3.5 * row['ATR_20'], 5.00) 
                new_trailing_stop = highest_close_since_entry - stop_distance
                stop_price = max(stop_price, new_trailing_stop)

        # --- LOOK FOR NEW ENTRIES ---
        if not in_trade and prev_row['Setup_Active']:
            limit_price = prev_row['EMA_50']
            
            if row['Gold_Low'] <= limit_price:
                entry_price = limit_price - 0.50 
                stop_distance = max(3.5 * row['ATR_20'], 5.00) 
                stop_price = entry_price - stop_distance
                highest_close_since_entry = entry_price
                
                risk_amount = equity * risk_pct
                position_size_oz = risk_amount / stop_distance
                
                if position_size_oz < 1.0: position_size_oz = 1.0 
                
                actual_risk = position_size_oz * stop_distance
                if actual_risk > (equity * 0.12):
                    position_size_oz = np.floor((equity * 0.12) / stop_distance)
                    if position_size_oz < 1.0: position_size_oz = 0
                
                if position_size_oz > 0: in_trade = True

        equity_curve.append(equity)

    # --- PERFORMANCE METRICS ---
    eq_series = pd.Series(equity_curve, index=df.index[1:])
    trade_df = pd.DataFrame(trades)
    
    total_return = (equity / start_equity) - 1
    days = len(equity_curve)
    cagr = ((1 + total_return) ** (252/days)) - 1 if days > 0 and total_return > 0 else 0
    
    rolling_max = eq_series.cummax()
    drawdown = (eq_series - rolling_max) / rolling_max
    max_dd = drawdown.min()

    print("=" * 50)
    print(f"V6 STRUCTURAL BEAST | Start: ${start_equity} | Risk: {risk_pct*100}%")
    print("=" * 50)
    print(f"Total Return:      {total_return*100:.2f}%")
    print(f"CAGR:              {cagr*100:.2f}%")
    print(f"Max Drawdown:      {max_dd*100:.2f}%")
    print(f"Final Equity:      ${equity:.2f}")
    print("=" * 50)
    
    # --- ERA BREAKDOWN ---
    print("\nWalk-Forward Era Breakdown (Profit Factor by Year Group):")
    trade_df['Era_Group'] = (trade_df['Era'] // 2) * 2 
    
    era_stats = []
    for era, group in trade_df.groupby('Era_Group'):
        wins = len(group[group['PnL'] > 0])
        losses = len(group[group['PnL'] < 0])
        total = wins + losses
        win_rate = wins / total if total > 0 else 0
        avg_win = group[group['PnL'] > 0]['PnL'].mean() if wins > 0 else 0
        avg_loss = abs(group[group['PnL'] < 0]['PnL'].mean()) if losses > 0 else 0
        pf = (win_rate * avg_win) / ((1-win_rate) * avg_loss) if avg_loss > 0 else np.inf
        
        era_stats.append({
            'Era': f"{era}-{era+1}",
            'Trades': total,
            'Win Rate': f"{win_rate*100:.1f}%",
            'Profit Factor': round(pf, 2)
        })
    
    era_df = pd.DataFrame(era_stats)
    print(era_df.to_string(index=False))

    return eq_series, trade_df

# ==========================================
# 4. RUN V6
# ==========================================
run_backtest_v6(df, start_equity=1500, risk_pct=0.03, normal_spread_usd=0.50)