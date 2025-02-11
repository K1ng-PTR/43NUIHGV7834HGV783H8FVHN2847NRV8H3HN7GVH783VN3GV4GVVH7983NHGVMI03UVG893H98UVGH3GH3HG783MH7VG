from dotenv import load_dotenv
import os
import random
import string
import datetime
from datetime import timedelta
from flask import Flask, request, jsonify

# Carrega as variáveis do arquivo .env
load_dotenv()

app = Flask(__name__)

# A senha secreta para geração de chaves é obtida do .env
SUPER_PASSWORD = os.environ.get("GEN_PASSWORD")
if not SUPER_PASSWORD or len(SUPER_PASSWORD) != 500:
    raise Exception("A variável de ambiente GEN_PASSWORD deve estar definida com exatamente 500 caracteres.")

# Armazenamento em memória para as chaves geradas
keys_data = {}

def generate_key():
    """Gera uma chave no formato 'XXXXX-XXXXX-XXXXX-XXXXX'."""
    groups = []
    for _ in range(4):
        group = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        groups.append(group)
    return '-'.join(groups)

@app.route('/gerar', methods=['POST'])
def gerar():
    """
    Endpoint protegido que gera uma nova chave.
    É necessário enviar no cabeçalho 'X-Gen-Password' a senha secreta de 500 caracteres.
    O JSON de entrada deve conter o campo "tipo" com valor "Uso Único" ou "LifeTime".
    """
    # Verifica se o token secreto está correto
    provided_password = request.headers.get("X-Gen-Password", "")
    if provided_password != SUPER_PASSWORD:
        return jsonify({"error": "Acesso não autorizado"}), 401

    data = request.get_json()
    if not data or 'tipo' not in data:
        return jsonify({"error": "O campo 'tipo' é obrigatório."}), 400

    tipo = data.get("tipo")
    if tipo not in ["Uso Único", "LifeTime"]:
        return jsonify({"error": "Tipo inválido. Deve ser 'Uso Único' ou 'LifeTime'."}), 400

    chave = generate_key()
    now = datetime.datetime.now()
    # Define a expiração em 6 horas a partir da geração
    expire_at = now + timedelta(hours=6)
    keys_data[chave] = {
        "tipo": tipo,
        "generated": now.isoformat(),
        "expire_at": expire_at.isoformat(),
        "used": False  # Só relevante para "Uso Único"
    }

    return jsonify({
        "chave": chave,
        "tipo": tipo,
        "expire_at": expire_at.isoformat()
    }), 200

@app.route('/validate', methods=['POST'])
def validate():
    """
    Endpoint para validação de uma chave.
    Recebe um JSON com o campo "chave" e retorna se ela é válida ou não.
    Para chaves de "Uso Único", marca como utilizada.
    Para chaves "LifeTime", estende a expiração para um prazo muito longo.
    """
    data = request.get_json()
    if not data or 'chave' not in data:
        return jsonify({"error": "O campo 'chave' é obrigatório."}), 400

    chave = data.get("chave")
    registro = keys_data.get(chave)
    if not registro:
        return jsonify({"valid": False, "message": "Chave inválida."}), 400

    now = datetime.datetime.now()
    expire_at = datetime.datetime.fromisoformat(registro["expire_at"])
    if now > expire_at:
        keys_data.pop(chave, None)
        return jsonify({"valid": False, "message": "Chave expirada."}), 400

    if registro["tipo"] == "Uso Único":
        if registro["used"]:
            return jsonify({"valid": False, "message": "Chave já utilizada."}), 400
        registro["used"] = True
    elif registro["tipo"] == "LifeTime":
        far_future = now + timedelta(days=365 * 100)
        registro["expire_at"] = far_future.isoformat()

    return jsonify({
        "valid": True,
        "tipo": registro["tipo"],
        "expire_at": registro["expire_at"],
        "message": "Chave validada com sucesso."
    }), 200

@app.route('/', methods=['GET'])
def index():
    return jsonify({"message": "API de chaves rodando."})

if __name__ == '__main__':
    app.run(host="0.0.0.0")
