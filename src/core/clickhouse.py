import threading
from typing import Any
from clickhouse_driver import Client
from src.core.config import settings

_local = threading.local()

def get_ch_client() -> Client:
    if not hasattr(_local, 'client'):
        _local.client = Client(
            host=settings.clickhouse_host,
            port=settings.clickhouse_port,
            database=settings.clickhouse_db,
            user=settings.clickhouse_user,
            password=settings.clickhouse_password,
            connect_timeout=10,
            send_receive_timeout=30,
            sync_request_timeout=5,
        )
    return _local.client

def insert_rows(table: str, rows: list[dict], columns: list[str]) -> int:
    """Batch insert. Returns number of rows inserted."""
    if not rows:
        return 0
    client = get_ch_client()
    data = [tuple(row[col] for col in columns) for row in rows]
    col_list = ', '.join(columns)
    client.execute(
        f'INSERT INTO {table} ({col_list}) VALUES',
        data
    )
    return len(rows)

def query(sql: str, params: dict | None = None) -> list[dict]:
    """Execute SELECT, return list of dicts."""
    client = get_ch_client()
    rows, columns_info = client.execute(sql, params or {}, with_column_types=True)
    col_names = [col[0] for col in columns_info]
    return [dict(zip(col_names, row)) for row in rows]
