# VANTA
### Independent Research & Development Project

## Short description

VANTA is a research platform exploring how information, economic events and market behaviour interact, with the aim of identifying and testing systematic trading opportunities.
## Long description

VANTA is an independent financial research and development project focused on understanding how financial markets respond to new information.

The project begins with the collection and analysis of historical macroeconomic events and foreign exchange market data, investigating how economic announcements, consensus forecasts and unexpected outcomes influence currency movements, volatility and market behaviour.

Using historical datasets, statistical analysis and predictive modelling, VANTA aims to identify measurable relationships between economic information and market reactions, develop systematic trading strategies and evaluate their performance through rigorous historical backtesting.

The initial research will cover the 28 currency pairs formed by the eight major currencies: USD, EUR, GBP, JPY, AUD, NZD, CAD and CHF. The platform will initially use one-minute candle data, with the possibility of integrating tick-level data later to improve execution modelling and investigate short-term market behaviour.

The longer-term objective is to develop a flexible research infrastructure capable of incorporating additional data sources, financial instruments and modelling techniques.

## Vanta research project checklist

### Phase 1: Acquire the historical economic calendar
#### First priority
- [ ] Inspect the Hugging Face dataset repository and confirm the actual downloadable files.
- [ ] Confirm the dataset's licence and permitted use.
- [ ] Inspect the CSV columns, date format, currency codes and impact labels.
- [ ] Check how actual, forecast and previous values are represented.
- [ ] Download the original dataset into data/raw/.
- [ ] Preserve the original file unchanged.
- [ ] Record the download date, source URL, dataset version or commit and file hash.
- [ ] Filter the working dataset to events from 1 January 2020 onwards.
- [ ] Retain high-impact events across all available currencies.
- [ ] Identify duplicate events, missing timestamps and malformed records.
Important limitation: a historical calendar snapshot does not necessarily show what the market knew before each event. Forecasts and previous values may have been updated after the event. We will preserve the source data, but we must not automatically treat it as a point-in-time record.

### Phase 2: Build the database
- [ ] Create the project directory structure.
- [ ] Set up SQLite for economic events, source records and observations.
- [ ] Set up DuckDB for analysing candle data stored in Parquet.
- [ ] Create a collection_runs table to track every ingestion process.
- [ ] Create an events table for event identity and scheduled release details.
- [ ] Create an event_sources table to preserve provider-specific records.
- [ ] Create an event_observations table for forecast, previous and actual values.
- [ ] Preserve original numeric strings, such as 65K, 7.1M and 3.75%.
- [ ] Add normalised numeric values and units where possible.
- [ ] Support multiple observations of the same event over time.

Notes (defer until more relevant)
- DuckDB is useful once we start ingesting candle data (Phase 3/4). We can defer installing/using DuckDB until we have chosen a price provider and storage format.
- Calendar records can contain duplicates and revisions. Ingestion should preserve every raw source row for provenance, but we can de-duplicate observations deterministically during import. The exact event identity rules (and how to treat duplicates) should be revisited in Phase 4 when we match events to price reactions.
- Project scope starts with the 8 major currencies (USD, EUR, GBP, JPY, AUD, NZD, CAD, CHF). We can exclude CNY at first and consider adding it later.

### Phase 3: Acquire historical FX candle data

- [ ] Decide which currency pairs to include.
- [ ] Identify a historical price-data provider.
- [ ] Confirm coverage from January 2020 onwards.
- [ ] Confirm that timestamps are available in UTC.
- [ ] Confirm whether candles contain bid, ask or midpoint prices.
- [ ] Download one-minute candles.
- [ ] Store raw downloads separately from processed data.
- [ ] Check for missing periods, duplicate candles and invalid prices.
- [ ] Check weekend gaps, daylight-saving transitions and market closures.
- [ ] Match each economic event to the relevant currency pairs.
- [ ] Align candle timestamps with event release timestamps.

### Phase 4: Construct the research dataset

This is where the calendar and price data become useful together.
- [ ] Match each event to the currencies and pairs it could affect.
- [ ] Calculate pre-event reference prices.
- [ ] Calculate price changes after 1, 5, 15 and 30 minutes.
- [ ] Calculate price changes after 1 and 4 hours.
- [ ] Calculate maximum favourable and adverse movement.
- [ ] Calculate realised volatility around each release.
- [ ] Calculate the forecast surprise where actual and forecast values exist.
- [ ] Normalise surprises by historical event volatility or another appropriate scale.
- [ ] Record missing data rather than silently dropping it.
- [ ] Produce a quality-control report.
### Phase 5: Develop the three strategies

#### Strategy A: Forecast and surprise analysis
- [ ] Measure historical forecast accuracy.
- [ ] Calculate actual-versus-forecast surprises.
- [ ] Test whether positive and negative surprises correlate with currency direction.
- [ ] Separate pre-release forecast-based signals from post-release actual-surprise signals.
- [ ] Test different holding periods.
- [ ] Measure win rate, average return and risk-adjusted performance.

#### Strategy B: Macro-model directional prediction
- [ ] Define the information available before each release.
- [ ] Select explanatory variables.
- [ ] Build a simple baseline model.
- [ ] Train using chronological historical data.
- [ ] Test on later, unseen periods.
- [ ] Compare model predictions with actual market direction.
- [ ] Evaluate whether the predictions remain useful after trading costs.

#### Strategy C: Bidirectional news-volatility trading
- [ ] Identify high-impact events.
- [ ] Measure typical pre- and post-release volatility.
- [ ] Test simultaneous long and short entries.
- [ ] Test pending buy-stop and sell-stop orders.
- [ ] Test different entry distances and stop-loss levels.
- [ ] (Later) Use the historical event-reaction dataset to tune: entry distance, pending-order expiry, and max-hold time (instead of hard-coded minutes).
- [ ] Account for spread widening, slippage and commissions.
- [ ] Compare performance across event types and currency pairs.

### Phase 6: Backtesting and validation
- [ ] Build a reusable backtesting engine.
- [ ] Ensure all trades use only information available at the time.
- [ ] Include realistic spreads, commissions and slippage assumptions.
- [ ] Define position sizing and risk limits.
- [ ] Include stop-loss, take-profit and time-based exits.
- [ ] Prevent overlapping trades from unintentionally sharing capital.
- [ ] Split data chronologically into development, validation and test periods.
- [ ] Run out-of-sample tests.
- [ ] Test different market regimes.
- [ ] Measure total return, drawdown, Sharpe ratio, expectancy and profit factor.
- [ ] Check sensitivity to parameters and transaction costs.
- [ ] Compare results against a no-trade baseline and simple benchmarks.

### Phase 7: Tick data and execution refinement

This comes later, once the candle-based research identifies strategies worth investigating.
- [ ] Select a tick-data provider.
- [ ] Acquire bid/ask tick data around relevant events.
- [ ] Reconstruct price movement within one-minute candles.
- [ ] Re-test Strategy C using tick-level execution.
- [ ] Model spread changes and order-trigger behaviour.
- [ ] Investigate slippage and stop-order execution.
- [ ] Compare tick-based results with candle-based results.

### Recommended project structure

``
forex-research/
â”‚
â”œâ”€â”€ data/
â”‚   â”œâ”€â”€ raw/
â”‚   â”‚   â”œâ”€â”€ economic_calendar/
â”‚   â”‚   â””â”€â”€ price_data/
â”‚   â”œâ”€â”€ processed/
â”‚   â”‚   â”œâ”€â”€ economic_events/
â”‚   â”‚   â””â”€â”€ price_data/
â”‚   â””â”€â”€ research.duckdb
â”‚
â”œâ”€â”€ database/
â”‚   â””â”€â”€ schema.sql
â”‚
â”œâ”€â”€ scripts/
â”‚   â”œâ”€â”€ inspect_calendar.py
â”‚   â”œâ”€â”€ import_calendar.py
â”‚   â”œâ”€â”€ download_prices.py
â”‚   â”œâ”€â”€ clean_prices.py
â”‚   â”œâ”€â”€ build_event_dataset.py
â”‚   â””â”€â”€ validate_dataset.py
â”‚
â”œâ”€â”€ strategies/
â”‚   â”œâ”€â”€ strategy_a_surprise.py
â”‚   â”œâ”€â”€ strategy_b_macro_model.py
â”‚   â””â”€â”€ strategy_c_news_volatility.py
â”‚
â”œâ”€â”€ backtesting/
â”‚   â”œâ”€â”€ engine.py
â”‚   â”œâ”€â”€ execution.py
â”‚   â””â”€â”€ metrics.py
â”‚
â”œâ”€â”€ notebooks/
â”‚
â”œâ”€â”€ config.py
â”œâ”€â”€ requirements.txt
â””â”€â”€ README.md
``

Completion criteria
We should treat each phase as a milestone rather than rushing through the checklist.
Milestone	What must be true before moving on
1. Calendar ingestion	We understand the source schema and have imported the records without losing original values.
2. Database	Events, observations and source provenance are stored consistently.
3. Price ingestion	We have reliable one-minute candles with known timestamp and price conventions.
4. Research dataset	Each event can be matched to relevant price movements and quality checks pass.
5. Strategy development	Each strategy has a precise entry, exit and signal definition.
6. Backtesting	Results account for trading costs and use genuinely unseen data.
7. Tick refinement	Promising strategies have been retested using more realistic execution data.

A few decisions we should make before downloading prices
There are three details worth settling:
1. Currency pairs: I suggest starting with the 28 combinations of the eight major currencies (USD, EUR, GBP, JPY, AUD, NZD, CAD and CHF). We can add CNY separately because its trading hours, liquidity and data availability differ.
2. Candle prices: Ideally, obtain bid and ask candles. If the provider only supplies midpoint or one-sided prices, we need to account for that limitation in the backtest.
3. Historical calendar reliability: the Hugging Face dataset is our starting point, not unquestionable ground truth. We need to inspect its schema, licence and historical records before importing it.
Our immediate next task is Phase 1, not writing the full ingestion pipeline. We should inspect the Hugging Face repository and a sample of its actual data first. That will tell us what the importer needs to handle and prevent us building the database around assumed column names or formats.

