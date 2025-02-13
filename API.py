from dotenv import load_dotenv
import os
import random
import string
import datetime
from datetime import timedelta
from flask import Flask, request, jsonify
import stripe
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import discord
from discord.ext import commands
import threading
import asyncio

load_dotenv()

bot_ready_event = threading.Event()

app = Flask(__name__)

# --- Variáveis de Ambiente ---
SUPER_PASSWORD = os.environ.get("GEN_PASSWORD")
if not SUPER_PASSWORD or len(SUPER_PASSWORD) != 500:
    raise Exception("A variável de ambiente GEN_PASSWORD deve estar definida com exatamente 500 caracteres.")

WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
if not WEBHOOK_SECRET:
    raise Exception("A variável de ambiente STRIPE_WEBHOOK_SECRET deve estar definida.")

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise Exception("A variável de ambiente DISCORD_BOT_TOKEN deve estar definida.")

DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID")
if not DISCORD_CHANNEL_ID:
    raise Exception("A variável de ambiente DISCORD_CHANNEL_ID deve estar definida.")

# Variáveis para envio de email
SMTP_SERVER = os.environ.get("SMTP_SERVER")      # Ex: smtp.gmail.com
SMTP_PORT = os.environ.get("SMTP_PORT")          # Ex: 587
SMTP_USER = os.environ.get("SMTP_USER")
SMTP_PASS = os.environ.get("SMTP_PASS")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
if not all([SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASS, SENDER_EMAIL]):
    raise Exception("As variáveis de ambiente para email não estão definidas corretamente.")

# --- Armazenamento das Chaves ---
keys_data = {}      # Mapeia a chave gerada para seus detalhes.
session_keys = {}   # Mapeia o session_id da Stripe para a chave gerada.

def generate_key():
    """Gera uma chave no formato 'XXXXX-XXXXX-XXXXX-XXXXX'."""
    groups = []
    for _ in range(4):
        group = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        groups.append(group)
    return '-'.join(groups)

def send_email(to_email, subject, body):
    """Envia um email usando SMTP com o corpo em HTML."""
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'html'))
    try:
        server = smtplib.SMTP(SMTP_SERVER, int(SMTP_PORT))
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SENDER_EMAIL, to_email, msg.as_string())
        server.quit()
        print(f"Email enviado para {to_email}")
    except Exception as e:
        print("Erro ao enviar email:", e)

# --- Configuração do Bot do Discord ---
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Global para armazenar o loop do bot
discord_loop = None

@bot.event
async def on_ready():
    global discord_loop
    discord_loop = asyncio.get_running_loop()
    print(f"Bot {bot.user} conectado ao Discord!")

async def send_discord_embed(session_id, tipo, chave):
    """Envia um embed com as informações do pagamento para o canal configurado."""
    channel = bot.get_channel(int(DISCORD_CHANNEL_ID))
    if channel:
        embed = discord.Embed(title="Pagamento Finalizado",
                              description=f"Session ID: {session_id}",
                              color=discord.Color.green())
        embed.add_field(name="Tipo de Compra", value=tipo, inline=False)
        embed.add_field(name="Chave Gerada", value=chave, inline=False)
        await channel.send(embed=embed)
    else:
        print("Canal Discord não encontrado.")

# --- Endpoints da API ---

@app.route('/gerar/<int:quantidade>', methods=['POST'])
def gerar_multiplo(quantidade):
    if quantidade < 1 or quantidade > 300:
        return jsonify({"error": "Quantidade deve ser entre 1 e 300."}), 400
    provided_password = request.headers.get("X-Gen-Password", "")
    if provided_password != SUPER_PASSWORD:
        return jsonify({"error": "Acesso não autorizado"}), 401
    data = request.get_json()
    if not data or 'tipo' not in data:
        return jsonify({"error": "O campo 'tipo' é obrigatório."}), 400
    tipo = data.get("tipo")
    if tipo not in ["Uso Único", "LifeTime"]:
        return jsonify({"error": "Tipo inválido. Deve ser 'Uso Único' ou 'LifeTime'."}), 400
    chaves_geradas = []
    now = datetime.datetime.now()
    for _ in range(quantidade):
        chave = generate_key()
        expire_at = now + timedelta(days=1) if tipo == "Uso Único" else None
        chave_data = {
            "tipo": tipo,
            "generated": now.isoformat(),
            "expire_at": expire_at.isoformat() if expire_at else None,
            "used": False
        }
        keys_data[chave] = chave_data
        chaves_geradas.append({
            "chave": chave,
            "tipo": tipo,
            "expire_at": expire_at.isoformat() if expire_at else None
        })
    return jsonify({"chaves": chaves_geradas}), 200

@app.route('/validation', methods=['POST'])
def validate():
    data = request.get_json()
    if not data or 'chave' not in data:
        return jsonify({"error": "O campo 'chave' é obrigatório."}), 400
    chave = data.get("chave")
    registro = keys_data.get(chave)
    if not registro:
        return jsonify({"valid": False, "message": "Chave inválida."}), 400
    now = datetime.datetime.now()
    if registro["expire_at"]:
        expire_at = datetime.datetime.fromisoformat(registro["expire_at"])
        if now > expire_at:
            keys_data.pop(chave, None)
            return jsonify({"valid": False, "message": "Chave expirada."}), 400
    if registro["used"]:
        return jsonify({"valid": False, "message": "Chave já utilizada."}), 400
    registro["used"] = True
    return jsonify({
        "valid": True,
        "tipo": registro["tipo"],
        "expire_at": registro["expire_at"] if registro["expire_at"] else "Sem expiração",
        "message": "Chave validada com sucesso."
    }), 200

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "alive"}), 200

@app.route('/', methods=['GET', 'HEAD', 'POST'])
def index():
    return jsonify({"message": "API de chaves rodando."}), 200

@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError as e:
        return jsonify({"error": "Assinatura inválida"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]

        # Obter o e-mail do cliente
        customer_details = session.get("customer_details", {})
        email = customer_details.get("email")

        # Determinar o tipo de compra
        metadata = session.get("metadata", {})
        product_id = metadata.get("product_id", "")
        if product_id == "prod_RlN66JRR2CKeIb":
            tipo = "LifeTime"
        elif product_id == "prod_RlNgQjVMVm9Jm5":
            tipo = "Uso Único"
        else:
            tipo = "LifeTime"

        # Gerar a chave
        now = datetime.datetime.now()
        expire_at = now + timedelta(days=1) if tipo == "Uso Único" else None
        chave = generate_key()
        chave_data = {
            "tipo": tipo,
            "generated": now.isoformat(),
            "expire_at": expire_at.isoformat() if expire_at else None,
            "used": False
        }
        keys_data[chave] = chave_data
        session_id = session.get("id")
        session_keys[session_id] = chave
        print(f"Pagamento confirmado via Stripe. Session ID: {session_id}, Chave {tipo} gerada: {chave}")

        # Enviar o e-mail para o cliente
        if email:
            subject = "Sua Chave de Produto"
            body = f"""
            <h1>Obrigado pelo seu pagamento!</h1>
            <p>Tipo de compra: {tipo}</p>
            <p>Sua chave de licença: <strong>{chave}</strong></p>
            <p>Session ID: {session_id}</p>
            """
            send_email(email, subject, body)
            print(f"E-mail enviado para {email}")
        else:
            print("E-mail do cliente não encontrado.")

        # Enviar notificação no Discord
        async def schedule_embed():
            await send_discord_embed(session_id, tipo, chave)
        if discord_loop is not None:
            asyncio.run_coroutine_threadsafe(schedule_embed(), discord_loop)
        else:
            print("Loop do Discord não disponível.")

    return jsonify({"status": "success"}), 200

@app.route("/sucesso", methods=["GET"])
def sucesso():
    session_id = request.args.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id é necessário."}), 400
    chave = session_keys.get(session_id)
    if not chave:
        return jsonify({"error": "Chave não encontrada para a sessão fornecida."}), 404
    detalhes = keys_data.get(chave)
    if not detalhes:
        return jsonify({"error": "Detalhes da chave não encontrados."}), 404
    return jsonify({
        "message": "Pagamento realizado com sucesso!",
        "chave": chave,
        "detalhes": detalhes
    }), 200

def start_discord_bot():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == '__main__':
    threading.Thread(target=start_discord_bot, daemon=True).start()
    app.run(host="0.0.0.0")
