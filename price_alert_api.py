import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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

class EmailNotifier:
    def __init__(self):
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587
        self.sender_email = os.getenv('GMAIL_USER')
        self.password = os.getenv('GMAIL_APP_PASSWORD')

    def send_email(self, recipient, subject, message):
        try:
            msg = MIMEMultipart()
            msg['From'] = self.sender_email
            msg['To'] = recipient
            msg['Subject'] = subject

            msg.attach(MIMEText(message, 'plain'))

            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.sender_email, self.password)
            server.send_message(msg)
            server.quit()
            print(f"Email envoyé avec succès à {recipient}")
            return True
        except Exception as e:
            print(f"Erreur d'envoi d'email: {str(e)}")
            return False

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
        DATABASE_URL = os.environ.get('DATABASE_URL')
        if DATABASE_URL and DATABASE_URL.startswith('postgres://'):
            DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
        
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
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

            # Envoyer un email de confirmation si une adresse email est fournie
            if alert.email:
                email_notifier = EmailNotifier()
                subject = "Nouvelle alerte de prix créée"
                message = (
                    f"Une nouvelle alerte de prix a été créée pour {alert.product_name}\n\n"
                    f"Prix cible: {alert.target_price}€\n"
                    f"Notification si prix {'supérieur' if alert.notify_above else 'inférieur'}\n"
                )
                email_notifier.send_email(alert.email, subject, message)

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
            # Récupérer l'alerte avant de la désactiver
            cursor.execute('SELECT * FROM price_alerts WHERE id = %s', (alert_id,))
            alert = cursor.fetchone()
            
            if not alert:
                raise HTTPException(status_code=404, detail="Alerte non trouvée")
            
            cursor.execute(
                'UPDATE price_alerts SET is_active = FALSE WHERE id = %s',
                (alert_id,)
            )
            conn.commit()

            # Envoyer un email de confirmation si une adresse email existe
            if alert['email']:
                email_notifier = EmailNotifier()
                subject = "Alerte de prix désactivée"
                message = f"L'alerte de prix pour {alert['product_name']} a été désactivée."
                email_notifier.send_email(alert['email'], subject, message)

            return {"status": "success", "message": "Alerte désactivée"}
    finally:
        conn.close()

@app.post("/check-prices")
async def check_prices(prices: List[ProductPrice]):
    """Vérifie les prix actuels par rapport aux alertes"""
    conn = get_db_connection()
    email_notifier = EmailNotifier()
    
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
                            alert_info = {
                                "alert_id": alert['id'],
                                "product_name": price.name,
                                "current_price": price.current_price,
                                "target_price": alert['target_price']
                            }
                            triggered_alerts.append(alert_info)
                            
                            if alert['email']:
                                subject = f"Alerte Prix - {price.name}"
                                message = (
                                    f"Le prix de {price.name} a changé!\n\n"
                                    f"Prix actuel: {price.current_price}€\n"
                                    f"Prix cible: {alert['target_price']}€\n"
                                    f"Type d'alerte: {'Prix supérieur' if alert['notify_above'] else 'Prix inférieur'}\n\n"
                                    f"Voir le produit: {price.url}"
                                )
                                email_notifier.send_email(alert['email'], subject, message)

                            cursor.execute('''
                                UPDATE price_alerts 
                                SET last_notification = NOW() 
                                WHERE id = %s
                            ''', (alert['id'],))
            
            conn.commit()
            return {"alerts_triggered": triggered_alerts}
    finally:
        conn.close()
