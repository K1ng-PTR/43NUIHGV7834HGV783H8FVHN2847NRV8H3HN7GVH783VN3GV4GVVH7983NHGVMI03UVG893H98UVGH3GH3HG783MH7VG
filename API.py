from dotenv import load_dotenv
import os
import random
import string
import datetime
from datetime import timedelta
from flask import Flask, request, jsonify
import stripe
import threading
import asyncio

load_dotenv()

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

# --- Links de Checkout Estáticos ---
LINK_USO_UNICO = "https://buy.stripe.com/test_6oE9E70jrdL47cseV7"
LINK_LIFETIME  = "https://buy.stripe.com/test_8wM2bF1nv0YiaoEbIU"

# --- Armazenamento das Chaves e Compras ---
keys_data = {}      # Mapeia a chave gerada para seus detalhes.
session_keys = {}   # Mapeia o session_id da Stripe para um dicionário com a chave gerada e o ID da compra.
pending_buys = []   # Armazena as compras que ainda não foram enviadas para o Bot.

def generate_key():
    """Gera uma chave no formato 'XXXXX-XXXXX-XXXXX-XXXXX'."""
    groups = []
    for _ in range(4):
        group = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        groups.append(group)
    return '-'.join(groups)

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

@app.route('/buys', methods=['GET'])
def get_buys():
    global pending_buys
    compras = pending_buys.copy()
    pending_buys.clear()  # Limpa as compras após repassá-las ao Bot
    return jsonify(compras), 200

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

        # Extrai o metadado "checkout_link" para identificar o tipo de compra
        metadata = session.get("metadata", {})
        checkout_link = metadata.get("checkout_link", "")
        print(f"Checkout Link (metadata): {checkout_link}")

        if checkout_link == LINK_USO_UNICO:
            tipo = "Uso Único"
        elif checkout_link == LINK_LIFETIME:
            tipo = "LifeTime"
        else:
            tipo = "LifeTime"  # Valor padrão

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

        # Associa o session_id à chave gerada e guarda também o ID da compra
        session_id = session.get("id")
        session_keys[session_id] = {
            "chave": chave,
            "id_compra": session.get("id", "N/A")
        }
        print(f"Pagamento confirmado via Stripe. Session ID: {session_id}, Chave {tipo} gerada: {chave}")

        # Armazena as informações da compra para que o Bot envie para o Discord
        compra = {
            "comprador": session.get("customer_details", {}).get("email", "N/A"),
            "tipo_chave": tipo,
            "chave": chave,
            "id_compra": session.get("id", "N/A"),
            "preco": session.get("amount_total", "N/A"),
            "checkout_url": checkout_link
        }
        pending_buys.append(compra)

        return jsonify({"status": "success", "session_id": session_id, "chave": chave}), 200

    return jsonify({"status": "ignored"}), 200

@app.route("/sucesso", methods=["GET"])
def sucesso():
    session_id = request.args.get("session_id")
    if not session_id:
        return "<h1>Erro:</h1><p>session_id é necessário.</p>", 400
    data = session_keys.get(session_id)
    if not data:
        return "<h1>Erro:</h1><p>Chave não encontrada para a sessão fornecida.</p>", 404
    chave = data["chave"]
    id_compra = data["id_compra"]
    detalhes = keys_data.get(chave)
    if not detalhes:
        return "<h1>Erro:</h1><p>Detalhes da chave não encontrados.</p>", 404

    html = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Pagamento Confirmado</title>
      <link rel="preconnect" href="https://fonts.googleapis.com">
      <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
      <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;700&display=swap" rel="stylesheet">
      <style>
        body {{
          margin: 0;
          padding: 0;
          font-family: 'Montserrat', sans-serif;
          background: linear-gradient(135deg, #1c92d2, #f2fcfe);
          display: flex;
          align-items: center;
          justify-content: center;
          height: 100vh;
          color: #333;
        }}
        .card {{
          background: rgba(255, 255, 255, 0.9);
          backdrop-filter: blur(10px);
          border-radius: 15px;
          padding: 2rem;
          box-shadow: 0 8px 16px rgba(0, 0, 0, 0.2);
          max-width: 500px;
          text-align: center;
        }}
        .card h1 {{
          font-size: 2.5rem;
          margin-bottom: 0.5rem;
          color: #1c92d2;
        }}
        .card p {{
          font-size: 1.1rem;
          margin-bottom: 1rem;
        }}
        .key {{
          font-size: 1.8rem;
          font-weight: bold;
          color: #f2994a;
          background: #fff;
          padding: 0.5rem 1rem;
          border-radius: 5px;
          display: inline-block;
          margin: 1rem 0;
          letter-spacing: 0.1rem;
        }}
        .checkmark-container {{
          margin: 0 auto 1rem;
          width: 60px;
          height: 60px;
          animation: pop 0.6s ease-out;
        }}
        .checkmark {{
          width: 100%;
          height: 100%;
          fill: #4caf50;
        }}
        @keyframes pop {{
          0% {{ transform: scale(0); opacity: 0; }}
          60% {{ transform: scale(1.2); opacity: 1; }}
          100% {{ transform: scale(1); }}
        }}
      </style>
    </head>
    <body>
      <div class="card">
        <div class="checkmark-container">
          <svg class="checkmark" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52">
            <circle cx="26" cy="26" r="25" fill="none" stroke="#4caf50" stroke-width="2"/>
            <path fill="none" stroke="#4caf50" stroke-width="5" d="M14 27l7 7 16-16"/>
          </svg>
        </div>
        <h1>Pagamento Confirmado!</h1>
        <p>Obrigado por sua compra. Seu pagamento foi realizado com sucesso e sua licença é 100% autêntica.</p>
        <p>Tipo de compra: <strong>{detalhes["tipo"]}</strong></p>
        <p>Sua chave de licença:</p>
        <div class="key">{chave}</div>
        <p><strong>ID da Compra:</strong></p>
        <p style="word-break: break-all; text-align: center;">{id_compra}</p>
        <p>{ "Validade: " + detalhes["expire_at"] if detalhes["expire_at"] else "Sem expiração" }</p>
      </div>
    </body>
    </html>
    """
    return html

if __name__ == '__main__':
    app.run(host="0.0.0.0")
