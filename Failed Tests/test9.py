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

# NO MORE EXTERNAL MACRO DATA. Price is truth.
df = gold.dropna()

# ==========================================
# 2. FEATURE ENGINEERING (V7 - PURE PRICE)
# ==========================================
# A. Secular Filter: Price > 200-day SMA
df['SMA_200'] = df['Gold_Close'].rolling(200).mean()
df['Secular_Bull'] = df['Gold_Close'] > df['SMA_200']

# B. Trend Filter: 50 EMA > 200 EMA
df['EMA_50'] = df['Gold_Close'].ewm(span=50, adjust=False).mean()
df['EMA_200'] = df['Gold_Close'].ewm(span=200, adjust=False).mean()
df['Trend_Up'] = df['EMA_50'] > df['EMA_200']

# C. Volatility Filters
# True Range calculation
high_low = df['Gold_High'] - df['Gold_Low']
high_close = np.abs(df['Gold_High'] - df['Gold_Close'].shift())
low_close = np.abs(df['Gold_Low'] - df['Gold_Close'].shift())
ranges = pd.concat([high_low, high_close, low_close], axis=1)
true_range = np.max(ranges, axis=1)

df['ATR_20'] = true_range.rolling(20).mean()
df['ATR_100'] = true_range.rolling(100).mean() # Long-term volatility baseline

# D. Volatility Expansion Gate: Short-term vol > Long-term vol
df['Vol_Expanding'] = df['ATR_20'] > df['ATR_100']

# E. Master Setup
df['Setup_Active'] = df['Secular_Bull'] & df['Trend_Up'] & df['Vol_Expanding']

df = df.dropna()

# ==========================================
# 3. BACKTEST ENGINE (V7)
# ==========================================
def run_backtest_v7(df, start_equity, risk_pct, normal_spread_usd):
    
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
    print(f"V7 PURE PRICE BEAST | Start: ${start_equity} | Risk: {risk_pct*100}%")
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
# 4. RUN V7
# ==========================================
run_backtest_v7(df, start_equity=1500, risk_pct=0.03, normal_spread_usd=0.50)