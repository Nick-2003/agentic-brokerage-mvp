# 1) real ask from Alpaca (yfinance is crumb-401'd; Alpaca is the live source)

import os; from dotenv import load_dotenv; load_dotenv()
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest
q=StockHistoricalDataClient(os.environ['ALPACA_API_KEY'],os.environ['ALPACA_API_SECRET']).get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols='F'))['F']
print('ask',q.ask_price,'-> limit',round(q.ask_price*1.01,2))