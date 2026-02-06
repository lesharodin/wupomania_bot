import sqlite3
import uuid
import requests
from datetime import datetime
from requests.auth import HTTPBasicAuth
import os
from logging_config import logger
from typing import Optional


YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")
YOOKASSA_API_URL = "https://api.yookassa.ru/v3/payments"



BASE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)

CLUB_DB_PATH = "/home/lesharodin/whoopclub_bot/database/bot.db"
logger.info(f"Using CLUB_DB_PATH = {CLUB_DB_PATH}")




def create_payment(
    *,
    user_id: int,
    amount: int,
    target_type: str,
    target_id: int,
    chat_id: int,
    message_id: int,
    payment_method: str = "sbp",
    description: Optional[str] = None,
) -> str:

    """
    Создаёт платёж в YooKassa и запись в payments.
    Возвращает confirmation_url.
    """

    now = datetime.now().isoformat()

    conn = sqlite3.connect(CLUB_DB_PATH)
    cursor = conn.cursor()

    # 1️⃣ записываем payment в БД
    cursor.execute("""
        INSERT INTO payments (
            user_id,
            amount,
            currency,
            payment_method,
            status,
            target_type,
            target_id,
            chat_id,
            message_id,
            ui_status,
            created_at
        )
        VALUES (?, ?, 'RUB', ?, 'pending', ?, ?, ?, ?, 'shown', ?)
    """, (
        user_id,
        amount,
        payment_method,
        target_type,
        target_id,
        chat_id,
        message_id,
        now
    ))

    payment_id = cursor.lastrowid
    conn.commit()

    # 2️⃣ запрос в YooKassa
    payload = {
        "amount": {
            "value": f"{amount:.2f}",
            "currency": "RUB"
        },
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": "https://whoopclub.ru/"
        },
        "description": description or f"Оплата {target_type}",
        "metadata": {
            "payment_id": str(payment_id)
        }
    }

    payload["payment_method_data"] = {"type": payment_method}

    headers = {
        "Idempotence-Key": str(uuid.uuid4()),
        "Content-Type": "application/json"
    }

    response = requests.post(
        YOOKASSA_API_URL,
        json=payload,
        headers=headers,
        auth=HTTPBasicAuth(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY),
        timeout=10
    )

    if response.status_code not in (200, 201):
        cursor.execute(
            "UPDATE payments SET status = 'canceled' WHERE id = ?",
            (payment_id,)
        )
        conn.commit()
        conn.close()
        raise RuntimeError(response.text)

    data = response.json()

    # 3️⃣ сохраняем yookassa_payment_id
    cursor.execute("""
        UPDATE payments
        SET yookassa_payment_id = ?
        WHERE id = ?
    """, (data["id"], payment_id))

    conn.commit()
    conn.close()

    return data["confirmation"]["confirmation_url"]
