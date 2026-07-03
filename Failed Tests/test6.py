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

tip_shifted = tip.shift(1)
dxy_shifted = dxy.shift(1)

df = gold.join(tip_shifted).join(dxy_shifted).dropna()

# ==========================================
# 2. FEATURE ENGINEERING (V4)
# ==========================================
# A. Regime Filter 1: TIP 60-day ROC > 0
df['TIP_ROC_60'] = df['TIP_Close'].pct_change(60)
df['Regime_TIP'] = df['TIP_ROC_60'] > 0 

# B. Regime Filter 2: DXY 60-day ROC < 0
df['DXY_ROC_60'] = df['DXY_Close'].pct_change(60)
df['Regime_DXY'] = df['DXY_ROC_60'] < 0

# C. Master Macro Regime
df['Macro_Bullish'] = df['Regime_TIP'] & df['Regime_DXY']

# D. Trend & Value Zone: 50 EMA
df['EMA_50'] = df['Gold_Close'].ewm(span=50, adjust=False).mean()
df['EMA_50_Slope'] = df['EMA_50'] > df['EMA_50'].shift(5)

# E. Setup Condition: Macro is Bullish, Trend is Up
df['Setup_Active'] = df['Macro_Bullish'] & df['EMA_50_Slope']

# F. Volatility (ATR)
high_low = df['Gold_High'] - df['Gold_Low']
high_close = np.abs(df['Gold_High'] - df['Gold_Close'].shift())
low_close = np.abs(df['Gold_Low'] - df['Gold_Close'].shift())
ranges = pd.concat([high_low, high_close, low_close], axis=1)
true_range = np.max(ranges, axis=1)
df['ATR_20'] = true_range.rolling(20).mean()

df = df.dropna()

# ==========================================
# 3. BACKTEST ENGINE (V4 - LIMIT ORDER LOGIC)
# ==========================================
def run_backtest_v4(df, start_equity, risk_pct, normal_spread_usd, stress_spread_usd, use_stress_spread=False, annual_swap_rate=-0.005):
    
    equity = start_equity
    in_trade = False
    entry_price = 0
    stop_price = 0
    position_size_oz = 0
    highest_close_since_entry = 0
    
    equity_curve = []
    trades = []
    
    # In V4, stress spread ONLY applies to the exit (stop loss hit). 
    # Limit orders protect the entry price.
    entry_spread = 0.50 # Minimal friction for limit order fill
    exit_spread = stress_spread_usd if use_stress_spread else normal_spread_usd
    
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i-1]
        
        # --- 1. MANAGE OPEN TRADES ---
        if in_trade:
            # A. Daily Holding Cost (Swap)
            daily_swap_rate = annual_swap_rate / 365
            swap_cost = (position_size_oz * row['Gold_Close']) * daily_swap_rate
            equity -= abs(swap_cost)
            
            # B. Stop Loss Hit (Chandelier Exit) - Applied with Asymmetric Exit Spread
            if row['Gold_Low'] <= stop_price:
                exit_price = stop_price - exit_spread 
                pnl = (exit_price - entry_price) * position_size_oz
                equity += pnl
                
                trades.append({
                    'Entry': entry_price, 'Exit': exit_price, 'PnL': pnl, 
                    'Size_oz': position_size_oz, 'Result': 'Stop', 'Equity': equity
                })
                in_trade = False

            # C. Update Chandelier Trailing Stop & Track Equity
            else:
                highest_close_since_entry = max(highest_close_since_entry, row['Gold_Close'])
                stop_distance = max(3.0 * row['ATR_20'], 5.00) 
                new_trailing_stop = highest_close_since_entry - stop_distance
                stop_price = max(stop_price, new_trailing_stop)

        # --- 2. LOOK FOR NEW ENTRIES (LIMIT ORDER LOGIC) ---
        # If we are flat, and the setup was active yesterday, we place a limit at yesterday's 50 EMA
        if not in_trade and prev_row['Setup_Active']:
            limit_price = prev_row['EMA_50']
            
            # Did price pierce our limit today?
            if row['Gold_Low'] <= limit_price:
                entry_price = limit_price # Filled exactly at the limit
                # Deduct small entry friction
                entry_price -= entry_spread 
                
                # Stop is placed 3.5 ATR below entry
                stop_distance = max(3.5 * row['ATR_20'], 5.00) 
                stop_price = entry_price - stop_distance
                highest_close_since_entry = entry_price
                
                # Position Sizing: Risk 2% of Equity
                risk_amount = equity * risk_pct
                position_size_oz = risk_amount / stop_distance
                
                if position_size_oz < 1.0:
                    position_size_oz = 1.0 
                
                actual_risk = position_size_oz * stop_distance
                if actual_risk > (equity * 0.10):
                    pass # Skip trade, too leveraged
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
    print(f"V4 RESULTS | Start: ${start_equity} | Stress: {use_stress_spread}")
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

print("\n--- TEST 1: V4 Properly Capitalized ($10,000) ---")
run_backtest_v4(df, start_equity=10000, risk_pct=0.02, normal_spread_usd=0.50, stress_spread_usd=20.00, use_stress_spread=False)

print("\n--- TEST 2: V4 $1500 Starting Capital ---")
run_backtest_v4(df, start_equity=1500, risk_pct=0.02, normal_spread_usd=0.50, stress_spread_usd=20.00, use_stress_spread=False)

print("\n--- TEST 3: V4 Worst-Case Spread Stress Test ($10k) ---")
run_backtest_v4(df, start_equity=10000, risk_pct=0.02, normal_spread_usd=0.50, stress_spread_usd=20.00, use_stress_spread=True)