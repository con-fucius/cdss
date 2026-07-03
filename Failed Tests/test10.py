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

df = gold.dropna()

# ==========================================
# 2. FEATURE ENGINEERING (V8 - TURTLE BREAKOUT)
# ==========================================
# A. Regime Filter: Close > 200-day SMA
df['SMA_200'] = df['Gold_Close'].rolling(200).mean()
df['Secular_Bull'] = df['Gold_Close'] > df['SMA_200']

# B. Breakout Indicator: 50-Day High
df['High_50'] = df['Gold_High'].rolling(50).max().shift(1)

# C. Exit Indicator: 20-Day Low
df['Low_20'] = df['Gold_Low'].rolling(20).min().shift(1)

# D. Volatility (ATR)
high_low = df['Gold_High'] - df['Gold_Low']
high_close = np.abs(df['Gold_High'] - df['Gold_Close'].shift())
low_close = np.abs(df['Gold_Low'] - df['Gold_Close'].shift())
ranges = pd.concat([high_low, high_close, low_close], axis=1)
true_range = np.max(ranges, axis=1)
df['ATR_20'] = true_range.rolling(20).mean()

df = df.dropna()

# ==========================================
# 3. BACKTEST ENGINE (V8)
# ==========================================
def run_backtest_v8(df, start_equity, risk_pct, entry_slippage, exit_slippage, use_stress_exit=False):
    
    equity = start_equity
    in_trade = False
    entry_price = 0
    stop_price = 0
    position_size_oz = 0
    
    equity_curve = []
    trades = []
    
    actual_exit_slippage = 20.00 if use_stress_exit else exit_slippage
    
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
            
            # 1. Stop Loss Hit (Intraday)
            if row['Gold_Low'] <= stop_price:
                exit_price = stop_price - actual_exit_slippage 
                pnl = (exit_price - entry_price) * position_size_oz
                equity += pnl
                
                trades.append({
                    'Date': row.name, 'Entry': entry_price, 'Exit': exit_price, 
                    'PnL': pnl, 'Equity': equity, 'Era': row.name.year, 'Result': 'Stop'
                })
                in_trade = False

            # 2. Trend Exit (Close < 20-day Low)
            elif prev_row['Gold_Close'] < prev_row['Low_20']:
                exit_price = row['Gold_Open'] - actual_exit_slippage
                pnl = (exit_price - entry_price) * position_size_oz
                equity += pnl
                
                trades.append({
                    'Date': row.name, 'Entry': entry_price, 'Exit': exit_price, 
                    'PnL': pnl, 'Equity': equity, 'Era': row.name.year, 'Result': 'Trend Exit'
                })
                in_trade = False

        # --- LOOK FOR NEW ENTRIES ---
        # Condition: Secular Bull, and yesterday closed above the 50-day high
        if not in_trade and prev_row['Secular_Bull'] and prev_row['Gold_Close'] > prev_row['High_50']:
            
            # We place a Buy Stop at yesterday's 50-day high.
            # We only get filled if today's price action pushes through that level.
            buy_stop_price = prev_row['High_50'] + entry_slippage
            
            if row['Gold_High'] >= buy_stop_price:
                # Filled!
                entry_price = buy_stop_price
                stop_distance = max(2.0 * row['ATR_20'], 2.00) # Tight 2x ATR stop, floor at $2
                stop_price = entry_price - stop_distance
                
                risk_amount = equity * risk_pct
                position_size_oz = risk_amount / stop_distance
                
                if position_size_oz < 1.0: position_size_oz = 1.0 
                
                actual_risk = position_size_oz * stop_distance
                if actual_risk > (equity * 0.10):
                    position_size_oz = np.floor((equity * 0.10) / stop_distance)
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

    stress_tag = "STRESS EXIT" if use_stress_exit else "NORMAL"
    print("=" * 50)
    print(f"V8 TURTLE BREAKOUT | Start: ${start_equity} | Risk: {risk_pct*100}% | {stress_tag}")
    print("=" * 50)
    print(f"Total Return:      {total_return*100:.2f}%")
    print(f"CAGR:              {cagr*100:.2f}%")
    print(f"Max Drawdown:      {max_dd*100:.2f}%")
    print(f"Final Equity:      ${equity:.2f}")
    print(f"Total Trades:      {len(trade_df)}")
    print("=" * 50)
    
    # --- ERA BREAKDOWN ---
    if len(trade_df) > 0:
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
# 4. RUN V8
# ==========================================
print("\n--- V8 Realistic Test ---")
run_backtest_v8(df, start_equity=1500, risk_pct=0.02, entry_slippage=0.50, exit_slippage=0.50, use_stress_exit=False)

print("\n--- V8 Stress Test (Modeling $20 Exit Spread only) ---")
# We keep entry slippage low (0.50) because we use Buy Stop limit orders.
# We model the $20 stress only on the exit, because that's where the broker robs you.
run_backtest_v8(df, start_equity=1500, risk_pct=0.02, entry_slippage=0.50, exit_slippage=0.50, use_stress_exit=True)