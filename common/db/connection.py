import sqlite3


def get_connection(db_path: str):
    return sqlite3.connect(db_path, check_same_thread=False)


def close_connection(conn) -> None:
    conn.close()