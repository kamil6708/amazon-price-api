from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import os
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Chargement des variables d'environnement
load_dotenv()

app = FastAPI(title="Amazon Price Alert API")

class PriceAlert(BaseModel):
    product_name: str
    target_price: float
    notify_above: bool = False
    notify_below: bool = True
    webhook_url: Optional[str] = None
    email: Optional[str] = None

class ProductPrice(BaseModel):
    name: str
    current_price: float
    timestamp: datetime
    url: str

def get_db_connection():
    """Crée une connexion à la base de données PostgreSQL"""
    try:
        conn = psycopg2.connect(
            os.environ.get('DATABASE_URL'),
            cursor_factory=RealDictCursor
        )
        return conn
    except Exception as e:
        print(f"Erreur de connexion à la base de données: {e}")
        raise HTTPException(status_code=500, detail="Erreur de connexion à la base de données")

def init_db():
    """Initialise la base de données avec les tables nécessaires"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS price_alerts (
                    id SERIAL PRIMARY KEY,
                    product_name TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    notify_above BOOLEAN NOT NULL,
                    notify_below BOOLEAN NOT NULL,
                    webhook_url TEXT,
                    email TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    last_notification TIMESTAMP
                )
            ''')
            conn.commit()
    finally:
        conn.close()

@app.on_event("startup")
async def startup_event():
    """Initialise la base de données au démarrage de l'application"""
    init_db()

@app.get("/")
async def root():
    """Route de test"""
    return {"message": "Amazon Price Alert API is running", "version": "1.0.0"}

@app.post("/alerts/")
async def create_alert(alert: PriceAlert):
    """Crée une nouvelle alerte de prix"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                INSERT INTO price_alerts 
                (product_name, target_price, notify_above, notify_below, webhook_url, email)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            ''', (
                alert.product_name,
                alert.target_price,
                alert.notify_above,
                alert.notify_below,
                alert.webhook_url,
                alert.email
            ))
            alert_id = cursor.fetchone()['id']
            conn.commit()
            return {
                "status": "success",
                "message": "Alerte créée avec succès",
                "alert_id": alert_id
            }
    finally:
        conn.close()

@app.get("/alerts/")
async def get_alerts():
    """Récupère toutes les alertes actives"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM price_alerts WHERE is_active = TRUE')
            alerts = cursor.fetchall()
            return list(alerts)
    finally:
        conn.close()

@app.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: int):
    """Désactive une alerte existante"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                'UPDATE price_alerts SET is_active = FALSE WHERE id = %s',
                (alert_id,)
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Alerte non trouvée")
            conn.commit()
            return {"status": "success", "message": "Alerte désactivée"}
    finally:
        conn.close()

@app.post("/check-prices")
async def check_prices(prices: List[ProductPrice]):
    """Vérifie les prix actuels par rapport aux alertes"""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute('SELECT * FROM price_alerts WHERE is_active = TRUE')
            alerts = cursor.fetchall()
            
            triggered_alerts = []
            for alert in alerts:
                for price in prices:
                    if price.name == alert['product_name']:
                        should_notify = (
                            (alert['notify_above'] and price.current_price > alert['target_price']) or
                            (alert['notify_below'] and price.current_price < alert['target_price'])
                        )
                        if should_notify:
                            triggered_alerts.append({
                                "alert_id": alert['id'],
                                "product_name": price.name,
                                "current_price": price.current_price,
                                "target_price": alert['target_price']
                            })
                            
                            cursor.execute('''
                                UPDATE price_alerts 
                                SET last_notification = NOW() 
                                WHERE id = %s
                            ''', (alert['id'],))
            
            conn.commit()
            return {"alerts_triggered": triggered_alerts}
    finally:
        conn.close()
