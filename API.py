from dotenv import load_dotenv
import os
import random
import string
import datetime
from datetime import timedelta
from flask import Flask, request, jsonify
import stripe
import requests
import discord
from discord.ext import commands
import threading
import asyncio

load_dotenv()

# Evento para sinalizar que o bot está pronto
bot_ready_event = threading.Event()

app = Flask(__name__)

# --- Variáveis de Ambiente ---
SUPER_PASSWORD = os.environ.get("GEN_PASSWORD")
if not SUPER_PASSWORD or len(SUPER_PASSWORD) != 500:
    raise Exception("GEN_PASSWORD deve ter exatamente 500 caracteres.")

WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
if not WEBHOOK_SECRET:
    raise Exception("STRIPE_WEBHOOK_SECRET é necessário.")

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
if not DISCORD_BOT_TOKEN:
    raise Exception("DISCORD_BOT_TOKEN é necessário.")

DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID")
if not DISCORD_CHANNEL_ID:
    raise Exception("DISCORD_CHANNEL_ID é necessário.")

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
    bot_ready_event.set()

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
    """
    Gera múltiplas chaves manualmente.
    Requer header "X-Gen-Password" e JSON com "tipo": "Uso Único" ou "LifeTime".
    """
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
    """
    Valida uma chave.
    No JSON, informe: { "chave": "XXXXX-XXXXX-XXXXX-XXXXX" }.
    """
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
    """
    Processa o webhook da Stripe.
    Extrai o metadata (com "product_id") para definir o tipo de compra,
    gera a chave e agenda o envio de um embed via Discord com as informações do pagamento.
    """
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

        # Determinar o tipo de compra com base no metadata (product_id)
        metadata = session.get("metadata", {})
        product_id = metadata.get("product_id", "")
        
        # Exibe o ID do produto para debug (opcional)
        print(f"Product ID: {product_id}")
        
        if product_id == "prod_RlN66JRR2CKeIb":
            tipo = "LifeTime"
        elif product_id == "prod_RlNgQjVMVm9Jm5":
            tipo = "Uso Único"
        else:
            tipo = "LifeTime"
        
        # Armazena o tipo na sessão
        metadata["tipo"] = tipo
        session["metadata"] = metadata
        
        # Exibe o tipo da chave para debug (opcional)
        print(f"Tipo de chave: {tipo}")

        # Gerar a chave
        now_dt = datetime.datetime.now()
        expire_at = now_dt + timedelta(days=1) if tipo == "Uso Único" else None
        chave = generate_key()
        chave_data = {
            "tipo": tipo,
            "generated": now_dt.isoformat(),
            "expire_at": expire_at.isoformat() if expire_at else None,
            "used": False
        }
        keys_data[chave] = chave_data

        # Associa o session_id à chave gerada
        session_id = session.get("id")
        session_keys[session_id] = chave
        print(f"Pagamento confirmado via Stripe. Session ID: {session_id}, Chave {tipo} gerada: {chave}")

        # Agenda o envio do embed para o Discord
        async def schedule_embed():
            await send_discord_embed(session_id, tipo, chave)
        if discord_loop is None:
            print("Loop do Discord não disponível, aguardando bot_ready_event...")
            bot_ready_event.wait(timeout=10)
        if discord_loop is not None:
            asyncio.run_coroutine_threadsafe(schedule_embed(), discord_loop)
        else:
            print("Loop do Discord ainda não disponível, embed não enviado.")

    return jsonify({"status": "success"}), 200

@app.route("/sucesso", methods=["GET"])
def sucesso():
    """
    Retorna uma página HTML personalizada com os detalhes do pagamento (chave, tipo, etc).
    Essa página é exibida após a compra e espera o parâmetro "session_id" na URL.
    """
    session_id = request.args.get("session_id")
    if not session_id:
        return "<h1>Erro:</h1><p>session_id é necessário.</p>", 400
    chave = session_keys.get(session_id)
    if not chave:
        return "<h1>Erro:</h1><p>Chave não encontrada para a sessão fornecida.</p>", 404
    detalhes = keys_data.get(chave)
    if not detalhes:
        return "<h1>Erro:</h1><p>Detalhes da chave não encontrados.</p>", 404

    html = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Pagamento Realizado com Sucesso</title>
      <style>
        body {{
          background-color: #f2f2f2;
          font-family: Arial, sans-serif;
          display: flex;
          justify-content: center;
          align-items: center;
          height: 100vh;
          margin: 0;
        }}
        .container {{
          background-color: #fff;
          padding: 30px;
          border-radius: 8px;
          box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
          max-width: 600px;
          width: 90%;
        }}
        h1 {{
          color: #333;
        }}
        p {{
          color: #555;
          font-size: 1.1em;
        }}
        .chave {{
          font-size: 1.4em;
          color: #007BFF;
          font-weight: bold;
        }}
      </style>
    </head>
    <body>
      <div class="container">
        <h1>Pagamento Realizado com Sucesso!</h1>
        <p>Tipo de compra: {detalhes["tipo"]}</p>
        <p>Sua chave de licença é: <span class="chave">{chave}</span></p>
        <p>Session ID: {session_id}</p>
        <p>{ "Validade: " + detalhes["expire_at"] if detalhes["expire_at"] else "Sem expiração" }</p>
      </div>
    </body>
    </html>
    """
    return html

def start_discord_bot():
    bot.run(DISCORD_BOT_TOKEN)

if __name__ == '__main__':
    threading.Thread(target=start_discord_bot, daemon=True).start()
    app.run(host="0.0.0.0")
