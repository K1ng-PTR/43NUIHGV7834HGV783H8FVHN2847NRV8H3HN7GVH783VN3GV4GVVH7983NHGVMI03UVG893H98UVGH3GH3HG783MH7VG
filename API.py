from dotenv import load_dotenv
import os
import random
import string
import datetime
from datetime import timedelta
from flask import Flask, request, jsonify
import stripe

load_dotenv()

app = Flask(__name__)

# --- Variáveis de ambiente ---
SUPER_PASSWORD = os.environ.get("GEN_PASSWORD")
if not SUPER_PASSWORD or len(SUPER_PASSWORD) != 500:
    raise Exception("A variável de ambiente GEN_PASSWORD deve estar definida com exatamente 500 caracteres.")

WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
if not WEBHOOK_SECRET:
    raise Exception("A variável de ambiente STRIPE_WEBHOOK_SECRET deve estar definida.")

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# --- Armazenamento das chaves geradas ---
# keys_data: mapeia a chave gerada para seus detalhes.
keys_data = {}
# session_keys: mapeia o session_id da Stripe para a chave gerada.
session_keys = {}

def generate_key():
    """Gera uma chave no formato 'XXXXX-XXXXX-XXXXX-XXXXX'."""
    groups = []
    for _ in range(4):
        group = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        groups.append(group)
    return '-'.join(groups)

# --- Endpoint para geração manual de chaves ---
@app.route('/gerar/<int:quantidade>', methods=['POST'])
def gerar_multiplo(quantidade):
    """
    Gera múltiplas chaves.
    - Cabeçalho: X-Gen-Password com o valor definido em GEN_PASSWORD.
    - Body JSON: { "tipo": "Uso Único" } ou { "tipo": "LifeTime" }.
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
        if tipo == "Uso Único":
            expire_at = now + timedelta(days=1)
        else:
            expire_at = None

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

# --- Endpoint para validação de chaves ---
@app.route('/validation', methods=['POST'])
def validate():
    """
    Valida a chave enviada.
    Body JSON: { "chave": "XXXXX-XXXXX-XXXXX-XXXXX" }
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

# --- Endpoints básicos ---
@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "alive"}), 200

@app.route('/', methods=['GET', 'HEAD', 'POST'])
def index():
    return jsonify({"message": "API de chaves rodando."})

# --- Endpoint do Webhook da Stripe ---
@app.route("/stripe-webhook", methods=["POST"])
def stripe_webhook():
    """
    Recebe eventos da Stripe e gera a chave correspondente.
    Para identificar o tipo de chave, utiliza o campo "product_id" definido em metadata:
      - Se product_id for "prod_RlN66JRR2CKeIb": gera chave do tipo LifeTime.
      - Se product_id for "prod_RlNgQjVMVm9Jm5": gera chave do tipo Uso Único (expira em 1 dia).
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

        # Obter o ID do produto a partir do metadata da sessão
        product_id = session.get("metadata", {}).get("product_id", "")
        if product_id == "prod_RlN66JRR2CKeIb":
            tipo = "LifeTime"
        elif product_id == "prod_RlNgQjVMVm9Jm5":
            tipo = "Uso Único"
        else:
            # Valor padrão se o product_id não for reconhecido
            tipo = "LifeTime"

        now = datetime.datetime.now()
        if tipo == "Uso Único":
            expire_at = now + timedelta(days=1)
        else:
            expire_at = None

        chave = generate_key()
        chave_data = {
            "tipo": tipo,
            "generated": now.isoformat(),
            "expire_at": expire_at.isoformat() if expire_at else None,
            "used": False
        }
        keys_data[chave] = chave

        # Armazena o mapeamento do session_id para a chave gerada
        session_id = session.get("id")
        session_keys[session_id] = chave

        print(f"Pagamento confirmado via Stripe. Session ID: {session_id}, Chave {tipo} gerada: {chave}")

    return jsonify({"status": "success"}), 200

# --- Endpoint para exibir a chave após o pagamento ---
@app.route("/sucesso", methods=["GET"])
def sucesso():
    """
    Exibe a chave gerada após o pagamento.
    Espera o parâmetro "session_id" na URL, que foi definido na success_url da sessão do Stripe.
    """
    session_id = request.args.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id é necessário."}), 400

    chave = session_keys.get(session_id)
    if not chave:
        return jsonify({"error": "Chave não encontrada para a sessão fornecida."}), 404

    # Recupera os detalhes da chave
    detalhes = keys_data.get(chave)
    if not detalhes:
        return jsonify({"error": "Detalhes da chave não encontrados."}), 404

    return jsonify({
        "message": "Pagamento realizado com sucesso!",
        "chave": chave,
        "detalhes": detalhes
    }), 200

if __name__ == '__main__':
    app.run(host="0.0.0.0")
