import pandas as pd
import numpy as np
import yfinance as yf
import datetime

# 1. Define Date Range (20 years for robust walk-forward)
end_date = datetime.datetime.now()
start_date = end_date - datetime.timedelta(days=20*365)

# 2. Fetch Gold Data (XAUUSD)
# Using Gold futures (GC=F) for reliable long-term history, but we model spot costs
print("Fetching Gold data...")
gold = yf.download('GC=F', start=start_date, end=end_date, auto_adjust=True)
gold = gold[['Open', 'High', 'Low', 'Close']]
gold.columns = ['Gold_Open', 'Gold_High', 'Gold_Low', 'Gold_Close']

# 3. Fetch Macro Data from Yahoo Finance (no API key needed)
# ^TNX = 10-Year Treasury Yield
# TIP ETF tracks TIPS, used as proxy for breakeven inflation
print("Fetching Macro data...")
macro = yf.download(['^TNX', 'TIP'], start=start_date, end=end_date, auto_adjust=True)
fred_data = macro['Close'].rename(columns={'^TNX': 'DGS10', 'TIP': 'T10YIE'})
fred_data = fred_data.ffill()

# 4. Data Alignment & Look-Ahead Bias Prevention
# We shift macro data by 1 day. We only know yesterday's closing yield for certain at today's open.
fred_data_shifted = fred_data.shift(1)

# Merge Gold and Macro
df = gold.join(fred_data_shifted).dropna()

# 5. Feature Engineering
print("Engineering features...")

# A. Real Yield Calculation
df['Real_Yield'] = df['DGS10'] - df['T10YIE']

# B. Regime Filter: 60-day Rate of Change (ROC) of Real Yield
df['Real_Yield_ROC_60'] = df['Real_Yield'].pct_change(60)

# C. Price TSMOM: 50 & 200 Exponential Moving Averages
df['EMA_50'] = df['Gold_Close'].ewm(span=50, adjust=False).mean()
df['EMA_200'] = df['Gold_Close'].ewm(span=200, adjust=False).mean()

# D. Volatility & Risk: 20-day Average True Range (ATR)
high_low = df['Gold_High'] - df['Gold_Low']
high_close = np.abs(df['Gold_High'] - df['Gold_Close'].shift())
low_close = np.abs(df['Gold_Low'] - df['Gold_Close'].shift())
ranges = pd.concat([high_low, high_close, low_close], axis=1)
true_range = np.max(ranges, axis=1)
df['ATR_20'] = true_range.rolling(20).mean()

# 6. Signal Logic Generation
# Regime: Real Yield ROC must be negative (Macro tailwind)
df['Regime_Bullish'] = df['Real_Yield_ROC_60'] < 0 

# Momentum: Price structure must be bullish
df['TSMOM_Bullish'] = (df['Gold_Close'] > df['EMA_50']) & (df['EMA_50'] > df['EMA_200'])

# Synthesis: Both conditions must be true to trade
df['Trade_Signal'] = df['Regime_Bullish'] & df['TSMOM_Bullish']

# Drop NaNs from rolling calculations
df = df.dropna()

print("Dataset ready. Shape:", df.shape)
print(df.tail())