import pandas as pd
import numpy as np
import yfinance as yf
import datetime

# --- Fetch data (same as test.py) ---
end_date = datetime.datetime.now()
start_date = end_date - datetime.timedelta(days=20*365)

print("Fetching Gold data...")
gold = yf.download('GC=F', start=start_date, end=end_date, auto_adjust=True)
gold = gold[['Open', 'High', 'Low', 'Close']]
gold.columns = ['Gold_Open', 'Gold_High', 'Gold_Low', 'Gold_Close']

print("Fetching Macro data...")
macro = yf.download(['^TNX', 'TIP'], start=start_date, end=end_date, auto_adjust=True)
fred_data = macro['Close'].rename(columns={'^TNX': 'DGS10', 'TIP': 'T10YIE'})
fred_data = fred_data.ffill()
fred_data = fred_data.shift(1)

df = gold.join(fred_data).dropna()
df['Real_Yield'] = df['DGS10'] - df['T10YIE']
df['Real_Yield_ROC_60'] = df['Real_Yield'].pct_change(60)
df['EMA_50'] = df['Gold_Close'].ewm(span=50, adjust=False).mean()
df['EMA_200'] = df['Gold_Close'].ewm(span=200, adjust=False).mean()

high_low = df['Gold_High'] - df['Gold_Low']
high_close = np.abs(df['Gold_High'] - df['Gold_Close'].shift())
low_close = np.abs(df['Gold_Low'] - df['Gold_Close'].shift())
ranges = pd.concat([high_low, high_close, low_close], axis=1)
true_range = np.max(ranges, axis=1)
df['ATR_20'] = true_range.rolling(20).mean()

df['Regime_Bullish'] = df['Real_Yield_ROC_60'] < 0
df['TSMOM_Bullish'] = (df['Gold_Close'] > df['EMA_50']) & (df['EMA_50'] > df['EMA_200'])
df['Trade_Signal'] = df['Regime_Bullish'] & df['TSMOM_Bullish']
df = df.dropna()

print("Dataset ready. Shape:", df.shape)

def run_backtest(df, start_equity=770, risk_pct=0.01, 
                 normal_spread_usd=0.30, stress_spread_usd=20.00, 
                 use_stress_spread=False, annual_swap_rate=-0.005):
    
    # Backtest State Variables
    equity = start_equity
    in_trade = False
    entry_price = 0
    stop_price = 0
    position_size_oz = 0
    max_close_since_entry = 0
    exit_pending = False
    
    # Tracking
    equity_curve = []
    trades = []
    
    # Spread logic: Use stress spread for stress testing
    current_spread = stress_spread_usd if use_stress_spread else normal_spread_usd
    
    for i in range(1, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i-1]
        
        # --- 1. MANAGE OPEN TRADES ---
        if in_trade:
            # A. Calculate Daily Holding Cost (Swap)
            # Long swap is typically negative. We deduct it daily.
            daily_swap_rate = annual_swap_rate / 365
            swap_cost = (position_size_oz * row['Gold_Close']) * daily_swap_rate
            equity -= abs(swap_cost)
            
            # B. Check for Stop Loss Hit (Intraday execution assumption)
            # If the low breaches the stop, we exit at the stop price minus spread
            if row['Gold_Low'] <= stop_price:
                exit_price = stop_price - current_spread # Slippage on stop
                pnl = (exit_price - entry_price) * position_size_oz
                equity += pnl
                
                trades.append({
                    'Entry': entry_price, 'Exit': exit_price, 'PnL': pnl, 
                    'Result': 'Stop', 'Equity': equity
                })
                in_trade = False
                
            # C. Check for Signal Exit (End of Day execution)
            # If signal was false yesterday, we exit at today's open minus spread
            elif exit_pending:
                exit_price = row['Gold_Open'] - current_spread
                pnl = (exit_price - entry_price) * position_size_oz
                equity += pnl
                
                trades.append({
                    'Entry': entry_price, 'Exit': exit_price, 'PnL': pnl, 
                    'Result': 'Signal Exit', 'Equity': equity
                })
                in_trade = False
                exit_pending = False
                
            # D. Manage Trailing Stop (If still in trade at End of Day)
            else:
                # Update Max Close
                max_close_since_entry = max(max_close_since_entry, row['Gold_Close'])
                
                # Calculate new trailing stop distance (2x ATR, floored at $3.00)
                stop_distance = max(2 * row['ATR_20'], 3.00)
                new_trailing_stop = max_close_since_entry - stop_distance
                
                # Stop only moves UP, never down
                stop_price = max(stop_price, new_trailing_stop)
                
                # If the signal flips, we queue an exit for the next day's open
                if not prev_row['Trade_Signal']:
                    exit_pending = True

        # --- 2. LOOK FOR NEW ENTRIES ---
        if not in_trade and prev_row['Trade_Signal']:
            # Volatility Gate: Don't trade if ATR is too low (spread eats the edge)
            if row['ATR_20'] < 10.00:
                pass # Skip trade, volatility too low
            else:
                # Enter at Next Day Open + Spread
                entry_price = row['Gold_Open'] + current_spread
                
                # Calculate Stop Distance (2x ATR, floored at $3.00)
                stop_distance = max(2 * row['ATR_20'], 3.00)
                stop_price = entry_price - stop_distance
                max_close_since_entry = entry_price
                
                # Position Sizing: Risk = 1% of Equity
                risk_amount = equity * risk_pct
                position_size_oz = risk_amount / stop_distance
                
                # Minimum lot size constraint (0.01 lots = 1 oz). 
                # If account is too small to take 1 oz with the risk parameters, skip trade.
                if position_size_oz < 1.0:
                    position_size_oz = 0 
                    pass # Skip trade, account too small for this stop distance
                else:
                    # Round down to nearest whole ounce (simulating 0.01 lot steps)
                    position_size_oz = np.floor(position_size_oz)
                    in_trade = True
                    exit_pending = False

        equity_curve.append(equity)

    # --- 3. CALCULATE PERFORMANCE METRICS ---
    eq_series = pd.Series(equity_curve)
    returns = eq_series.pct_change().dropna()
    
    # Calculate Drawdown
    rolling_max = eq_series.cummax()
    drawdown = (eq_series - rolling_max) / rolling_max
    max_dd = drawdown.min()
    
    # Trade Stats
    trade_df = pd.DataFrame(trades)
    if len(trade_df) > 0:
        win_rate = len(trade_df[trade_df['PnL'] > 0]) / len(trade_df)
        avg_win = trade_df[trade_df['PnL'] > 0]['PnL'].mean() if win_rate > 0 else 0
        avg_loss = abs(trade_df[trade_df['PnL'] < 0]['PnL'].mean()) if win_rate < 1 else 0
        profit_factor = (win_rate * avg_win) / ((1-win_rate) * avg_loss) if avg_loss > 0 else np.inf
    else:
        win_rate, profit_factor = 0, 0

    # Annualized Return (Assuming ~252 trading days)
    total_return = (equity / start_equity) - 1
    days = len(equity_curve)
    cagr = ((1 + total_return) ** (252/days)) - 1 if days > 0 else 0
    
    # Sharpe Ratio (Assuming 0% risk-free rate for simplicity)
    sharpe = (returns.mean() / returns.std()) * np.sqrt(252) if returns.std() > 0 else 0

    print("=" * 45)
    print(f"BACKTEST RESULTS | Spread: ${current_spread:.2f} | Start: ${start_equity}")
    print("=" * 45)
    print(f"Total Return:      {total_return*100:.2f}%")
    print(f"CAGR:              {cagr*100:.2f}%")
    print(f"Max Drawdown:      {max_dd*100:.2f}%")
    print(f"Sharpe Ratio:      {sharpe:.2f}")
    print(f"Total Trades:      {len(trade_df)}")
    print(f"Win Rate:          {win_rate*100:.2f}%")
    print(f"Profit Factor:     {profit_factor:.2f}")
    print(f"Final Equity:      ${equity:.2f}")
    print("=" * 45)

    return eq_series, trade_df

# --- RUN THE SIMULATIONS ---

# 1. REALISTIC ECN TEST (Normal Market Conditions)
print("\nRunning Realistic ECN Simulation...")
eq_realistic, trades_realistic = run_backtest(df, use_stress_spread=False)

# 2. STRESS TEST (Modeling NFP/FOMC 200-pip spread on EVERY trade)
print("\nRunning Extreme Stress Simulation...")
eq_stress, trades_stress = run_backtest(df, use_stress_spread=True)