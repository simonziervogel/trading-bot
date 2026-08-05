"""Kalshi API client — RSA-PSS signed requests for Production and Demo endpoints."""

import os
import base64
import datetime
import requests
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

load_dotenv()


class KalshiClient:
    """HTTP client for the Kalshi Trade API v2.

    Authenticates every request with RSA-PSS SHA-256 signatures.
    Credentials are read from environment variables (KEY_ID, KEY_FILE, BASE_URL).
    """

    def __init__(self):
        self.api_key_id = os.getenv("KALSHI_API_KEY_ID")
        key_file = os.getenv("KALSHI_KEY_FILE")
        self.base_url = os.getenv("KALSHI_BASE_URL")
        self.private_key = self._load_private_key(key_file)

    def _load_private_key(self, file_path):
        with open(file_path, "rb") as key_file:
            return serialization.load_pem_private_key(
                key_file.read(),
                password=None,
                backend=default_backend()
            )

    def _sign_request(self, method, path):
        """Return signed auth headers for a given HTTP method and path."""
        timestamp = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
        timestamp_str = str(timestamp)
        path_without_query = path.split('?')[0]
        msg_string = timestamp_str + method + path_without_query
        message = msg_string.encode('utf-8')
        signature = self.private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH
            ),
            hashes.SHA256()
        )
        sig_b64 = base64.b64encode(signature).decode('utf-8')
        return {
            'KALSHI-ACCESS-KEY': self.api_key_id,
            'KALSHI-ACCESS-SIGNATURE': sig_b64,
            'KALSHI-ACCESS-TIMESTAMP': timestamp_str
        }

    def get_balance(self):
        path = '/trade-api/v2/portfolio/balance'
        headers = self._sign_request('GET', path)
        response = requests.get(self.base_url + path, headers=headers)
        return response.json()

    def get_markets(self, limit=1000, status='open', series_ticker=None):
        """Fetch markets with automatic pagination.

        Args:
            limit: Results per page (max 1000).
            status: Market status filter ('open', 'closed', 'settled').
            series_ticker: Optional series filter (e.g. 'KXBTC15M').
        """
        all_markets = []
        cursor = None

        while True:
            path = f'/trade-api/v2/markets?limit={limit}&status={status}'
            if cursor:
                path += f'&cursor={cursor}'
            if series_ticker:
                path += f'&series_ticker={series_ticker}'

            headers = self._sign_request('GET', path)
            response = requests.get(self.base_url + path, headers=headers)
            data = response.json()

            all_markets.extend(data.get('markets', []))
            cursor = data.get('cursor')
            if not cursor:
                break

        return {'markets': all_markets}

    def get_series_list(self, category=None, tags=None):
        """Fetch available series templates (e.g. KXBTC, elections)."""
        path = '/trade-api/v2/series'
        params = []
        if category:
            params.append(f'category={category}')
        if tags:
            params.append(f'tags={tags}')
        if params:
            path += '?' + '&'.join(params)

        headers = self._sign_request('GET', path)
        response = requests.get(self.base_url + path, headers=headers)
        return response.json()

    def get_historical_markets(self, limit=1000, status=None, series_ticker=None,
                               event_ticker=None, tickers=None, cursor=None):
        """Fetch historical (archived) markets for backtesting.

        Args:
            limit: Results per page (max 1000).
            status: Filter by status ('open', 'closed', 'settled').
            series_ticker: Filter by series (e.g. 'KXBTC-1W').
            event_ticker: Filter by single event.
            tickers: Comma-separated list of specific tickers.
            cursor: Pagination cursor.
        """
        path = f'/trade-api/v2/historical/markets?limit={limit}'
        if status:
            path += f'&status={status}'
        if series_ticker:
            path += f'&series_ticker={series_ticker}'
        if event_ticker:
            path += f'&event_ticker={event_ticker}'
        if tickers:
            path += f'&tickers={tickers}'
        if cursor:
            path += f'&cursor={cursor}'

        headers = self._sign_request('GET', path)
        response = requests.get(self.base_url + path, headers=headers)
        return response.json()

    def get_historical_trades(self, limit=1000, ticker=None, min_ts=None, max_ts=None,
                              cursor=None):
        """Fetch historical trades for archived markets."""
        path = f'/trade-api/v2/historical/trades?limit={limit}'
        if ticker:
            path += f'&ticker={ticker}'
        if min_ts is not None:
            path += f'&min_ts={int(min_ts)}'
        if max_ts is not None:
            path += f'&max_ts={int(max_ts)}'
        if cursor:
            path += f'&cursor={cursor}'

        headers = self._sign_request('GET', path)
        response = requests.get(self.base_url + path, headers=headers)
        return response.json()

    def get_historical_cutoff_timestamps(self):
        """Fetch the cutoff timestamps separating live and archived data."""
        path = '/trade-api/v2/historical/cutoff'
        headers = self._sign_request('GET', path)
        response = requests.get(self.base_url + path, headers=headers)
        return response.json()

    def get_historical_market_candlesticks(self, ticker, start_ts, end_ts, period_interval=1):
        """Fetch historical candlesticks for a specific archived market.

        Args:
            ticker: Market ticker.
            start_ts: Unix timestamp lower bound.
            end_ts: Unix timestamp upper bound.
            period_interval: Candle size in minutes (1, 60, or 1440).
        """
        if start_ts is None or end_ts is None:
            raise ValueError('start_ts and end_ts are required for historical candlesticks')

        path = (
            f'/trade-api/v2/historical/markets/{ticker}/candlesticks'
            f'?start_ts={int(start_ts)}&end_ts={int(end_ts)}'
            f'&period_interval={int(period_interval)}'
        )
        headers = self._sign_request('GET', path)
        response = requests.get(self.base_url + path, headers=headers)
        return response.json()
