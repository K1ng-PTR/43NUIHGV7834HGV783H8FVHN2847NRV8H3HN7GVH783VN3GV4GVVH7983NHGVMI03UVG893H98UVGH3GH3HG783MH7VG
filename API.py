#!/usr/bin/env python3
import os
import random
import string
import datetime
import hashlib
import sys
from datetime import timedelta
from flask import Flask, request, jsonify, render_template_string, redirect, url_for
from dotenv import load_dotenv
import stripe
from supabase import create_client, Client

load_dotenv()
app = Flask(__name__)

# === VARIÁVEIS DE AMBIENTE ===
SUPER_PASSWORD = os.environ.get("GEN_PASSWORD")
if not SUPER_PASSWORD or len(SUPER_PASSWORD) != 500:
    raise Exception("GEN_PASSWORD deve ter exatamente 500 caracteres.")

WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
if not WEBHOOK_SECRET:
    raise Exception("STRIPE_WEBHOOK_SECRET é necessário.")

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# Senha administrativa para acessar o subsite /auth-hwid
ADMIN_PASSWORD = "1U_eZAvFrH7IwI4yhVoiBr!4QMqh!ePKab.X4R1Am/xs0/kvxJ/uvb3.9HUHB1lhJ!XqTIGaH_pzV.KoJfyx/jwD8jc3Zh1n5ER.UKPqsYxfKTx5PJUGC4BTaq1RM3//8QfU5bJSfgzlDfXlF13Ql6BAgJ3KOLbsHi!.mt_U2oXao.Co_AwidbN9L.fj/Df_KUSHvlHfJD621OrQxsqP60-7HhdwqU6bQf/a4KaHcJD4Lk-mcAyOVkIsrJEgpswVMl-rY8cq5ZgONm4xKW2k!UPmPa1wqsxL!Mk-.ft/c-frL4R7WWYBiwvJiZ_WWHkQ_flgrWKAaCaovlNRKbl4unX.R1v_6av/vBJ-b-q/wMNBbTFgwvgHpso8xsDfwy7dCSPOAHJ7fmsDTBYKeY1Khj6B_Y.3_jjNJl5-GfIOS4MA/fsm7FlB0pdS3d/VTcU0iJad/DR9aGBux3DAaM/YNm/EtitvVgt9Yd!fu8-wya7HBrA7-pCi"

# Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise Exception("SUPABASE_URL e SUPABASE_KEY são necessários.")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Variáveis globais para armazenar compras e sessões pendentes
pending_buys = []
session_keys = {}

# === FUNÇÕES AUXILIARES ===
def generate_key():
    """Gera uma chave no formato 'XXXXX-XXXXX-XXXXX-XXXXX'."""
    groups = []
    for _ in range(4):
        group = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        groups.append(group)
    return '-'.join(groups)

def generate_activation_id(hwid, chave):
    """
    Combina HWID e chave, aplica SHA256 e retorna um número de 22 dígitos.
    Se o HWID estiver vazio, utiliza string vazia.
    """
    h = hashlib.sha256(f"{hwid}{chave}".encode()).hexdigest()
    num = int(h, 16) % (10**22)
    return str(num).zfill(22)

# === ENDPOINTS DA API ===

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
    # A data de ativação será definida somente na primeira validação
    for _ in range(quantidade):
        chave = generate_key()
        activation_id = generate_activation_id("", chave)
        registro = {
            "hwid": "",
            "chave": chave,
            "activation_id": activation_id,
            "data_ativacao": None,  # Será definida no momento da validação
            "tipo": tipo
        }
        res = supabase.table("activations").insert(registro).execute()
        if not res.data:
            return jsonify({"error": "Erro ao inserir registro no banco", "details": "Dados não retornados"}), 500
            
        chaves_geradas.append({
            "chave": chave,
            "tipo": tipo,
            "activation_id": activation_id,
            "data_ativacao": None
        })
    return jsonify({"chaves": chaves_geradas}), 200

@app.route('/validation', methods=['POST'])
def validate():
    data = request.get_json()
    if not data or 'chave' not in data or 'hwid' not in data:
        return jsonify({"error": "Os campos 'chave' e 'hwid' são obrigatórios."}), 400

    chave = data.get("chave")
    hwid_request = data.get("hwid")
    
    try:
        res = supabase.table("activations").select("*").eq("chave", chave).execute()
    except Exception as e:
        print("Erro ao consultar o banco:", e)
        return jsonify({"error": "Ocorreu um erro", "details": str(e)}), 500

    if not res.data:
        return jsonify({"valid": False, "message": "invalid Key."}), 400

    registro = res.data[0]

    # Se a licença foi revogada, instrui a app a apagar o license.json e reabrir o menu de ativação
    if registro.get("revoked"):
        return jsonify({
            "valid": False,
            "reset": True,
            "message": "Licença revogada. Por favor, apague license.json e reative a chave."
        }), 200

    # Se o registro já foi ativado (ou seja, já possui um HWID registrado)
    if registro.get("hwid"):
        if registro.get("hwid") != hwid_request:
            return jsonify({
                "valid": False,
                "message": "Autorização Recusada"
            }), 400

        expected_activation_id = generate_activation_id(hwid_request, chave)
        if registro.get("activation_id") != expected_activation_id:
            # A API informa que a licença foi atualizada (ex.: nova chave gerada pelo admin)
            return jsonify({
                "valid": False,
                "update": True,
                "new_data": registro,  # Envia os dados atuais do registro (com nova chave, data, etc.)
                "message": "Nova chave gerada. A licença será atualizada."
            }), 200

        # Para chaves do tipo "Uso Único", verifica expiração
        if registro.get("tipo") == "Uso Único":
            try:
                activation_date = datetime.datetime.fromisoformat(registro.get("data_ativacao"))
            except Exception as e:
                print("Erro ao converter data_ativacao:", e)
                return jsonify({"valid": False, "message": "Data de ativação inválida."}), 400
            expiration_date = activation_date + datetime.timedelta(days=1)
            if datetime.datetime.now() > expiration_date:
                return jsonify({"valid": False, "message": "Chave expirada."}), 400

        return jsonify({
            "valid": True,
            "tipo": registro.get("tipo"),
            "data_ativacao": registro.get("data_ativacao"),
            "activation_id": registro.get("activation_id"),
            "message": "Chave validada com sucesso."
        }), 200

    # Fluxo para primeira ativação (sem HWID definido)
    now_dt = datetime.datetime.now().isoformat()
    new_activation_id = generate_activation_id(hwid_request, chave)
    update_data = {
        "hwid": hwid_request,
        "activation_id": new_activation_id,
        "data_ativacao": now_dt
    }
    try:
        update_res = supabase.table("activations").update(update_data).eq("chave", chave).execute()
        if not update_res.data:
            print("Erro: atualização retornou dados vazios.")
            return jsonify({
                "error": "Erro ao atualizar registro",
                "details": "Dados não retornados"
            }), 500
    except Exception as e:
        print("Exceção ao atualizar registro:", e)
        return jsonify({
            "error": "Erro ao atualizar registro",
            "details": str(e)
        }), 500
        
    registro.update(update_data)
    return jsonify({
        "valid": True,
        "tipo": registro.get("tipo"),
        "data_ativacao": now_dt,
        "activation_id": new_activation_id,
        "message": "Chave validada com sucesso."
    }), 200

    # Fluxo para primeira ativação (sem HWID definido)
    now_dt = datetime.datetime.now().isoformat()
    new_activation_id = generate_activation_id(hwid_request, chave)
    update_data = {
        "hwid": hwid_request,
        "activation_id": new_activation_id,
        "data_ativacao": now_dt
    }
    try:
        update_res = supabase.table("activations").update(update_data).eq("chave", chave).execute()
        if not update_res.data:
            print("Erro: atualização retornou dados vazios.")
            return jsonify({
                "error": "Erro ao atualizar registro",
                "details": "Dados não retornados"
            }), 500
    except Exception as e:
        print("Exceção ao atualizar registro:", e)
        return jsonify({
            "error": "Erro ao atualizar registro",
            "details": str(e)
        }), 500
        
    registro.update(update_data)
    return jsonify({
        "valid": True,
        "tipo": registro.get("tipo"),
        "data_ativacao": now_dt,
        "activation_id": new_activation_id,
        "message": "Chave validada com sucesso."
    }), 200

@app.route('/buys', methods=['GET'])
def get_buys():
    global pending_buys
    compras = pending_buys.copy()
    pending_buys.clear()
    return jsonify(compras), 200

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
        metadata = session.get("metadata", {})
        checkout_link = metadata.get("checkout_link", "")
        # Define o tipo conforme sua lógica
        tipo = "Uso Único" if checkout_link == "https://buy.stripe.com/14k7tX60H3QE6kg14b" else "LifeTime"
        now_dt = datetime.datetime.now().isoformat()
        chave = generate_key()
        activation_id = generate_activation_id("", chave)
        registro = {
            "hwid": "",  # Ainda não vinculado
            "chave": chave,
            "activation_id": activation_id,
            "data_ativacao": now_dt,
            "tipo": tipo
        }
        try:
            res = supabase.table("activations").insert(registro).execute()
        except Exception as e:
            return jsonify({"error": "Erro ao inserir registro via Stripe", "details": str(e)}), 500

        if not res.data:
            return jsonify({"error": "Erro ao inserir registro via Stripe", "details": "Dados não retornados"}), 500

        session_id = session.get("id")
        # Armazena os dados da sessão para a página de sucesso
        session_keys[session_id] = {"chave": chave, "id_compra": session.get("id", "N/D")}
        compra = {
            "comprador": session.get("customer_details", {}).get("email", "N/D"),
            "tipo_chave": tipo,
            "chave": chave,
            "id_compra": session.get("id", "N/D"),
            "preco": session.get("amount_total", "N/D"),
            "checkout_url": checkout_link
        }
        pending_buys.append(compra)
        return jsonify({"status": "success", "session_id": session_id, "chave": chave}), 200
    return jsonify({"status": "ignored"}), 200

@app.route("/sucesso", methods=["GET"])
def sucesso():
    session_id = request.args.get("session_id")
    if not session_id:
        return "<h1>Error:</h1><p>session_id is required.</p>", 400
    data = session_keys.get(session_id)
    if not data:
        return "<h1>Error:</h1><p>Key not found for the provided session.</p>", 404
    chave = data["chave"]
    id_compra = data["id_compra"]
    res = supabase.table("activations").select("*").eq("chave", chave).execute()
    if not res.data:
        return "<h1>Error:</h1><p>Key details not found.</p>", 404
    registro = res.data[0]
    html = """
    <!DOCTYPE html>
    <html lang="pt">
    <head>
      <meta charset="UTF-8">
      <title>Chave de 5x5</title>
      <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&display=swap" rel="stylesheet" />
      <script src="https://cdn.tailwindcss.com"></script>
      <style>
        body {
          background: linear-gradient(135deg, #1c1b1b 0%, #2b2a2a 100%);
          display: flex;
          justify-content: center;
          align-items: center;
          height: 100vh;
          font-family: 'Playfair Display', serif;
          color: #f5e7c8;
        }
        .chave {
          font-size: 2em;
          border-radius: 50px;
          padding: 10px;
          background-color: #2b2b2b;
          border: 2px solid #bfa560;
          box-shadow: 0 0 10px rgba(191, 165, 96, 0.4);
          text-align: center;
          width: 560px;
          letter-spacing: 3px;
        }
        .botao-copiar {
          margin-top: 20px;
          padding: 10px 20px;
          background-color: #2b2b2b;
          border: 2px solid #bfa560;
          border-radius: 8px;
          color: #f5e7c8;
          cursor: pointer;
        }
      </style>
    </head>
    <body>
      <div class="container">
        <div class="chave" id="chave">{chave}</div>
        <button class="botao-copiar" onclick="copyKey()">Copiar Chave</button>
      </div>
      <script>
        function copyKey() {
          navigator.clipboard.writeText("{chave}");
          alert("Chave copiada!");
        }
      </script>
    </body>
    </html>
    """
    return html
    
# === ENDPOINTS ADMINISTRATIVOS (/auth-hwid) ===

DARK_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <title>Administração - Auth HWID</title>
    <style>
        body { background: linear-gradient(135deg, #1c1b1b 0%, #2b2a2a 100%); color: #f5e7c8; font-family: 'Arial', serif; display: flex; justify-content: center; align-items: center; height: 100vh; }
        .container { text-align: center; width: 600px !important; background-color: #2b2b2b; border: 2px solid #bfa560; box-shadow: 0 0 10px rgba(191, 165, 96, 0.4); padding: 20px; border-radius: 10px; }
        .adm {font-size: 20px;}
        input[type="password"], input[type="submit"] { width: 100%; padding: 10px; margin: 10px 0; border: none; border-radius: 5px; }
        input[type="password"] { background-color: #3a3a3a; color: #f5e7c8; }
        input[type="submit"] { background-color: #bfa560; color: #2b2b2b; cursor: pointer; font-weight: bold; }
        table { width: 100%; margin-top: 20px; border-collapse: collapse; }
        th, td { border: 1px solid #bfa560; padding: 8px; text-align: center; }
        th { background-color: #3a3a3a; }
        tr:nth-child(even) { background-color: #2b2b2b; }
        .hidden { display: none; }
    </style>
</head>
<body>
    <div class="container">
        {% if not authenticated %}
        <div id="login-box">
            <h2 class="adm">Admin Login</h2>
            <form method="post" action="{{ url_for('auth_hwid') }}">
                <input type="password" name="password" placeholder="Senha de Admin" required>
                <input type="submit" value="Entrar">
            </form>
        </div>
        {% else %}
        <h1>Registros de Ativação</h1>
        <table>
            <tr>
                <th>Activation ID</th>
                <th>Chave</th>
                <th>Tipo</th>
                <th>HWID</th>
                <th>Data de Ativação</th>
                <th>Ação</th>
            </tr>
            {% for r in records %}
            <tr>
                <td>{{ r.activation_id }}</td>
                <td>{{ r.chave }}</td>
                <td>{{ r.tipo }}</td>
                <td>{{ r.hwid or "N/D" }}</td>
                <td>{{ r.data_ativacao or "N/D" }}</td>
                <td>
                    {% if not r.authorized %}
                    <form method="post" action="{{ url_for('auth_hwid_authorize') }}">
                        <input type="hidden" name="activation_id" value="{{ r.activation_id }}">
                        <input type="hidden" name="password" value="{{ admin_password }}">
                        <input type="submit" value="Autorizar">
                    </form>
                    {% else %}
                        Autorizado
                    {% endif %}
                </td>
            </tr>
            {% endfor %}
        </table>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route("/auth-hwid", methods=["GET", "POST"])
def auth_hwid():
    authenticated = False
    admin_pass = None
    if request.method == "POST":
        admin_pass = request.form.get("password")
        if admin_pass == ADMIN_PASSWORD:
            authenticated = True
        else:
            return render_template_string(DARK_TEMPLATE, authenticated=False), 401
    else:
        admin_pass = request.args.get("password")
        if admin_pass == ADMIN_PASSWORD:
            authenticated = True
    if not authenticated:
        return render_template_string(DARK_TEMPLATE, authenticated=False)
    result = supabase.table("activations").select("*").execute()
    records = result.data if result.data else []
    return render_template_string(DARK_TEMPLATE, authenticated=True, records=records, admin_password=ADMIN_PASSWORD)

@app.route("/auth-hwid/authorize", methods=["POST"])
def auth_hwid_authorize():
    # Obter a senha administrativa (do form ou JSON)
    admin_pass = request.form.get("password") or (request.json or {}).get("password")
    if admin_pass != ADMIN_PASSWORD:
        return "<h1>Acesso não autorizado</h1>", 401

    # Obter o activation_id do registro a ser revogado
    activation_id_old = request.form.get("activation_id") or (request.json or {}).get("activation_id")
    if not activation_id_old:
        return "<h1>Activation ID não informado</h1>", 400

    # Consulta o registro antigo no Supabase
    res = supabase.table("activations").select("*").eq("activation_id", activation_id_old).execute()
    if not res.data:
        return "<h1>Registro não encontrado</h1>", 404

    # Marcar o registro antigo como revogado
    revoke_update = {"revoked": True}
    supabase.table("activations").update(revoke_update).eq("activation_id", activation_id_old).execute()

    # Gerar nova chave do tipo LifeTime (o client calculará ID, HWID, etc)
    new_key = generate_key()
    new_activation_id = generate_activation_id("", new_key)  # sem HWID
    new_record = {
        "hwid": "",  # Ainda não vinculado
        "chave": new_key,
        "activation_id": new_activation_id,
        "data_ativacao": None,  # Sem ativação ainda
        "tipo": "LifeTime",
        "revoked": False  # Nova licença válida
    }
    insert_res = supabase.table("activations").insert(new_record).execute()
    if not insert_res.data:
        return f"<h1>Erro ao inserir novo registro: {insert_res}</h1>", 500

    # Retornar a nova chave em HTML
    response_html = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <title>Nova Chave Gerada</title>
        <style>
            body {{
                background-color: #121212;
                color: #fff;
                font-family: Arial, sans-serif;
                text-align: center;
                padding-top: 50px;
            }}
            .container {{
                width: 80%;
                margin: auto;
            }}
            .key-box {{
                background-color: #1e1e1e;
                padding: 20px;
                border-radius: 8px;
                display: inline-block;
                margin-top: 20px;
                font-size: 1.5em;
                letter-spacing: 2px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Autorização Atualizada!</h1>
            <p>Sua licença atual foi revogada.</p>
            <p>A nova chave gerada é:</p>
            <div class="key-box">{new_key}</div>
            <p>Por favor, copie essa chave e reinicie a aplicação para reativar.</p>
        </div>
    </body>
    </html>
    """
    return response_html, 200

if __name__ == '__main__':
    app.run(host="0.0.0.0")
