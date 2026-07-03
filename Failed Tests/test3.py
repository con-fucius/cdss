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
# TIP ETF: Price goes UP when Real Yields drop. Perfect inverse proxy.
tip = yf.download('TIP', start=start_date, end=end_date, auto_adjust=True)
tip = tip[['Close']]
tip.columns = ['TIP_Close']

# Shift TIP by 1 day to prevent look-ahead bias (we only know yesterday's close)
tip_shifted = tip.shift(1)

# Merge
df = gold.join(tip_shifted).dropna()

# ==========================================
# 2. FEATURE ENGINEERING
# ==========================================
# A. Regime Filter: 60-day Rate of Change of TIP
# If TIP is trending up (ROC > 0), Real Yields are falling -> Bullish Macro Regime
df['TIP_ROC_60'] = df['TIP_Close'].pct_change(60)

# B. Price TSMOM: 50 & 200 Exponential Moving Averages
df['EMA_50'] = df['Gold_Close'].ewm(span=50, adjust=False).mean()
df['EMA_200'] = df['Gold_Close'].ewm(span=200, adjust=False).mean()

# C. Volatility & Risk: 20-day Average True Range (ATR)
high_low = df['Gold_High'] - df['Gold_Low']
high_close = np.abs(df['Gold_High'] - df['Gold_Close'].shift())
low_close = np.abs(df['Gold_Low'] - df['Gold_Close'].shift())
ranges = pd.concat([high_low, high_close, low_close], axis=1)
true_range = np.max(ranges, axis=1)
df['ATR_20'] = true_range.rolling(20).mean()

# D. Signal Logic
df['Regime_Bullish'] = df['TIP_ROC_60'] > 0 
df['TSMOM_Bullish'] = (df['Gold_Close'] > df['EMA_50']) & (df['EMA_50'] > df['EMA_200'])
df['Trade_Signal'] = df['Regime_Bullish'] & df['TSMOM_Bullish']

# Drop NaNs
df = df.dropna()

# Quick Diagnostic
signal_count = df['Trade_Signal'].sum()
print(f"Data Shape: {df.shape} | Total Bullish Signal Days: {signal_count}")

# ==========================================
# 3. BACKTEST ENGINE
# ==========================================
def run_backtest(df, start_equity, risk_pct, normal_spread_usd, stress_spread_usd, use_stress_spread=False, annual_swap_rate=-0.005):
    
    equity = start_equity
    in_trade = False
    entry_price = 0
    stop_price = 0
    position_size_oz = 0
    max_close_since_entry = 0
    exit_pending = False
    
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
            
            # B. Stop Loss Hit (Intraday)
            if row['Gold_Low'] <= stop_price:
                exit_price = stop_price - current_spread 
                pnl = (exit_price - entry_price) * position_size_oz
                equity += pnl
                
                trades.append({
                    'Entry': entry_price, 'Exit': exit_price, 'PnL': pnl, 
                    'Size_oz': position_size_oz, 'Result': 'Stop', 'Equity': equity
                })
                in_trade = False
                
            # C. Signal Exit (Next Day Open)
            elif exit_pending:
                exit_price = row['Gold_Open'] - current_spread
                pnl = (exit_price - entry_price) * position_size_oz
                equity += pnl
                
                trades.append({
                    'Entry': entry_price, 'Exit': exit_price, 'PnL': pnl, 
                    'Size_oz': position_size_oz, 'Result': 'Signal Exit', 'Equity': equity
                })
                in_trade = False
                exit_pending = False
                
            # D. Manage Trailing Stop
            else:
                max_close_since_entry = max(max_close_since_entry, row['Gold_Close'])
                stop_distance = max(2 * row['ATR_20'], 3.00)
                new_trailing_stop = max_close_since_entry - stop_distance
                stop_price = max(stop_price, new_trailing_stop)
                
                if not prev_row['Trade_Signal']:
                    exit_pending = True

        # --- 2. LOOK FOR NEW ENTRIES ---
        if not in_trade and prev_row['Trade_Signal']:
            if row['ATR_20'] < 10.00:
                pass # Volatility gate
            else:
                entry_price = row['Gold_Open'] + current_spread
                stop_distance = max(2 * row['ATR_20'], 3.00)
                stop_price = entry_price - stop_distance
                max_close_since_entry = entry_price
                
                # Position Sizing
                risk_amount = equity * risk_pct
                position_size_oz = risk_amount / stop_distance
                
                # FIX: If ideal size < 1 oz, force 1 oz (0.01 lots) to see the raw edge
                # This means we are risking slightly more than risk_pct on small accounts
                if position_size_oz < 1.0:
                    position_size_oz = 1.0 
                
                # Safety: If forcing 1 oz risks more than 10% of the account, SKIP.
                actual_risk = position_size_oz * stop_distance
                if actual_risk > (equity * 0.10):
                    pass # Skip trade, too leveraged for this account size
                else:
                    in_trade = True
                    exit_pending = False

        equity_curve.append(equity)

    # --- 3. CALCULATE PERFORMANCE METRICS ---
    if len(equity_curve) < 2:
        print("Not enough data points.")
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
    print(f"BACKTEST RESULTS | Start: ${start_equity} | Spread: ${current_spread}")
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

# TEST 1: Realistic ECN with proper capitalization ($10,000)
# This proves if the mathematical edge actually exists
print("\n--- TEST 1: Properly Capitalized Edge Test ---")
run_backtest(df, start_equity=10000, risk_pct=0.01, normal_spread_usd=0.30, stress_spread_usd=20.00, use_stress_spread=False)

# TEST 2: Your KES 100,000 Starting Capital ($770)
# This shows the reality of micro-lot constraints
print("\n--- TEST 2: $770 Starting Capital ---")
run_backtest(df, start_equity=770, risk_pct=0.01, normal_spread_usd=0.30, stress_spread_usd=20.00, use_stress_spread=False)

# TEST 3: Worst-Case Broker Spread ($20) on $10k account
# Stress test the edge itself
print("\n--- TEST 3: Worst-Case Spread Stress Test ---")
run_backtest(df, start_equity=10000, risk_pct=0.01, normal_spread_usd=0.30, stress_spread_usd=20.00, use_stress_spread=True)