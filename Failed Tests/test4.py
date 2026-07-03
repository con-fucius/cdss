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

print("Fetching Macro Proxy (TIP) data...")
tip = yf.download('TIP', start=start_date, end=end_date, auto_adjust=True)
tip = tip[['Close']]
tip.columns = ['TIP_Close']
tip_shifted = tip.shift(1)

df = gold.join(tip_shifted).dropna()

# ==========================================
# 2. FEATURE ENGINEERING (V2)
# ==========================================
# A. Regime Filter: TIP 60-day ROC > 0
df['TIP_ROC_60'] = df['TIP_Close'].pct_change(60)
df['Regime_Bullish'] = df['TIP_ROC_60'] > 0 

# B. Volatility Contraction Filter
# Today's ATR must be less than ATR 10 days ago
df['ATR_20'] = (df['Gold_High'] - df['Gold_Low']).rolling(20).mean() # Simplified ATR for speed
df['ATR_10_Lag'] = df['ATR_20'].shift(10)
df['Vol_Contracted'] = df['ATR_20'] < df['ATR_10_Lag']

# C. Donchian Breakout (20-Day High)
df['High_20'] = df['Gold_High'].rolling(20).max().shift(1)
df['Breakout'] = df['Gold_Close'] > df['High_20']

# D. Donchian Exit (10-Day Low)
df['Low_10'] = df['Gold_Low'].rolling(10).min().shift(1)
df['Trend_Broken'] = df['Gold_Close'] < df['Low_10']

# E. Master Signal
# We only go long if: Macro is bullish, Volatility is contracted, and Price breaks 20-day high
df['Trade_Signal'] = df['Regime_Bullish'] & df['Vol_Contracted'] & df['Breakout']

df = df.dropna()

# ==========================================
# 3. BACKTEST ENGINE (V2)
# ==========================================
def run_backtest_v2(df, start_equity, risk_pct, normal_spread_usd, stress_spread_usd, use_stress_spread=False, annual_swap_rate=-0.005):
    
    equity = start_equity
    in_trade = False
    entry_price = 0
    stop_price = 0
    position_size_oz = 0
    
    equity_curve = []
    trades = []
    
    current_spread = stress_spread_usd if use_stress_spread else normal_spread_usd
    
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i-1]
        
        # --- 1. MANAGE OPEN TRADES ---
        if in_trade:
            # A. Daily Holding Cost (Swap)
            daily_swap_rate = annual_swap_rate / 365
            swap_cost = (position_size_oz * row['Gold_Close']) * daily_swap_rate
            equity -= abs(swap_cost)
            
            # B. Stop Loss Hit
            if row['Gold_Low'] <= stop_price:
                exit_price = stop_price - current_spread 
                pnl = (exit_price - entry_price) * position_size_oz
                equity += pnl
                
                trades.append({
                    'Entry': entry_price, 'Exit': exit_price, 'PnL': pnl, 
                    'Size_oz': position_size_oz, 'Result': 'Stop', 'Equity': equity
                })
                in_trade = False
                
            # C. Trend Broken Exit (Close below 10-day low)
            elif prev_row['Trend_Broken']:
                exit_price = row['Gold_Open'] - current_spread
                pnl = (exit_price - entry_price) * position_size_oz
                equity += pnl
                
                trades.append({
                    'Entry': entry_price, 'Exit': exit_price, 'PnL': pnl, 
                    'Size_oz': position_size_oz, 'Result': 'Trend Exit', 'Equity': equity
                })
                in_trade = False

        # --- 2. LOOK FOR NEW ENTRIES ---
        if not in_trade and prev_row['Trade_Signal']:
            entry_price = row['Gold_Open'] + current_spread
            
            # Wider Stop: 3.5x ATR
            stop_distance = max(3.5 * row['ATR_20'], 5.00) # Floor at $5.00
            stop_price = entry_price - stop_distance
            
            # Position Sizing: Risking 2% of Equity
            risk_amount = equity * risk_pct
            position_size_oz = risk_amount / stop_distance
            
            # Force minimum 1 oz (0.01 lots)
            if position_size_oz < 1.0:
                position_size_oz = 1.0 
            
            # Safety: If 1 oz risks more than 10% of account, SKIP.
            actual_risk = position_size_oz * stop_distance
            if actual_risk > (equity * 0.10):
                pass 
            else:
                in_trade = True

        equity_curve.append(equity)

    # --- 3. CALCULATE PERFORMANCE METRICS ---
    if len(equity_curve) < 2:
        return pd.Series(), pd.DataFrame()
        
    eq_series = pd.Series(equity_curve)
    returns = eq_series.pct_change().dropna()
    
    rolling_max = eq_series.cummax()
    drawdown = (eq_series - rolling_max) / rolling_max
    max_dd = drawdown.min()
    
    trade_df = pd.DataFrame(trades)
    if len(trade_df) > 0:
        win_rate = len(trade_df[trade_df['PnL'] > 0]) / len(trade_df)
        avg_win = trade_df[trade_df['PnL'] > 0]['PnL'].mean() if win_rate > 0 else 0
        avg_loss = abs(trade_df[trade_df['PnL'] < 0]['PnL'].mean()) if win_rate < 1 else 0
        profit_factor = (win_rate * avg_win) / ((1-win_rate) * avg_loss) if avg_loss > 0 else np.inf
    else:
        win_rate, profit_factor = 0, 0

    total_return = (equity / start_equity) - 1
    days = len(equity_curve)
    cagr = ((1 + total_return) ** (252/days)) - 1 if days > 0 and total_return > 0 else 0
    sharpe = (returns.mean() / returns.std()) * np.sqrt(252) if returns.std() > 0 else 0

    print("=" * 50)
    print(f"V2 RESULTS | Start: ${start_equity} | Spread: ${current_spread}")
    print("=" * 50)
    print(f"Total Return:      {total_return*100:.2f}%")
    print(f"CAGR:              {cagr*100:.2f}%")
    print(f"Max Drawdown:      {max_dd*100:.2f}%")
    print(f"Sharpe Ratio:      {sharpe:.2f}")
    print(f"Total Trades:      {len(trade_df)}")
    print(f"Win Rate:          {win_rate*100:.2f}%")
    print(f"Profit Factor:     {profit_factor:.2f}")
    print(f"Final Equity:      ${equity:.2f}")
    print("=" * 50)

    return eq_series, trade_df

# ==========================================
# 4. RUN SIMULATIONS
# ==========================================

print("\n--- TEST 1: V2 Properly Capitalized ($10,000) ---")
run_backtest_v2(df, start_equity=10000, risk_pct=0.02, normal_spread_usd=0.30, stress_spread_usd=20.00, use_stress_spread=False)

print("\n--- TEST 2: V2 $1500 Starting Capital ---")
run_backtest_v2(df, start_equity=1500, risk_pct=0.02, normal_spread_usd=0.30, stress_spread_usd=20.00, use_stress_spread=False)

print("\n--- TEST 3: V2 Worst-Case Spread Stress Test ($10k) ---")
run_backtest_v2(df, start_equity=10000, risk_pct=0.02, normal_spread_usd=0.30, stress_spread_usd=20.00, use_stress_spread=True)