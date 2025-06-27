import mysql.connector
from mysql.connector import Error
import os
from dotenv import load_dotenv

load_dotenv()

def get_connection():
    try:
        connection = mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", 3306)),
            database=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS")
        )
        return connection
    except Error as e:
        print(f"[DB ERROR] {e}")
        return None


def execute_query(query, params=None):
    connection = get_connection()
    if connection is None:
        raise Exception("Database connection failed.")
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(query, params or ())
        connection.commit()
        return cursor.lastrowid
    finally:
        cursor.close()
        connection.close()


def fetch_all(query, params=None):
    connection = get_connection()
    if connection is None:
        raise Exception("Database connection failed.")
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(query, params or ())
        return cursor.fetchall()
    finally:
        cursor.close()
        connection.close()


def fetch_one(query, params=None):
    connection = get_connection()
    if connection is None:
        raise Exception("Database connection failed.")
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute(query, params or ())
        return cursor.fetchone()
    finally:
        cursor.close()
        connection.close()
