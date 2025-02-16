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
        if res.error:
            return jsonify({"error": "Erro ao inserir registro no banco", "details": res.error.message}), 500
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
        return jsonify({"error": "Erro ao consultar o banco", "details": str(e)}), 500

    if not res.data:
        return jsonify({"valid": False, "message": "Chave inválida."}), 400

    registro = res.data[0]

    # Se a chave já foi ativada (ou seja, já possui um HWID registrado)
    if registro.get("hwid"):
        if registro.get("hwid") != hwid_request:
            return jsonify({
                "valid": False,
                "message": "Autorização Recusada"
            }), 400

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
        tipo = "Uso Único" if checkout_link == "https://buy.stripe.com/test_6oE9E70jrdL47cseV7" else "LifeTime"
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
        return "<h1>Erro:</h1><p>session_id é necessário.</p>", 400
    data = session_keys.get(session_id)
    if not data:
        return "<h1>Erro:</h1><p>Chave não encontrada para a sessão fornecida.</p>", 404
    chave = data["chave"]
    id_compra = data["id_compra"]
    res = supabase.table("activations").select("*").eq("chave", chave).execute()
    if not res.data:
        return "<h1>Erro:</h1><p>Detalhes da chave não encontrados.</p>", 404
    registro = res.data[0]
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
      </style>
    </head>
    <body>
      <div class="card">
        <h1>Pagamento Confirmado!</h1>
        <p>Tipo de compra: <strong>{registro.get("tipo")}</strong></p>
        <p>Sua chave:</p>
        <div class="key">{chave}</div>
        <p><strong>Purchase ID:</strong></p>
        <p>{id_compra}</p>
        <p>Data de Ativação: <strong>{registro.get("data_ativacao")}</strong></p>
      </div>
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
        body { background-color: #121212; color: #ffffff; font-family: Arial, sans-serif; }
        .container { width: 90%; margin: auto; padding: 20px; }
        table { width: 100%; border-collapse: collapse; }
        th, td { border: 1px solid #333; padding: 8px; text-align: center; }
        th { background-color: #1e1e1e; }
        tr:nth-child(even) { background-color: #1e1e1e; }
        a.button { background-color: #EA5656; color: #fff; padding: 6px 12px; text-decoration: none; border-radius: 4px; }
        .login-box { margin: 50px auto; width: 300px; padding: 20px; background-color: #1e1e1e; border-radius: 8px; }
        input[type="password"] { width: 100%; padding: 8px; margin: 10px 0; }
        input[type="submit"] { background-color: #EA5656; color: #fff; border: none; padding: 10px; width: 100%; cursor: pointer; }
    </style>
</head>
<body>
    <div class="container">
        {% if not authenticated %}
        <div class="login-box">
            <h2>Admin Login</h2>
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
                    {% if r.hwid %}
                    <form method="post" action="{{ url_for('auth_hwid_authorize') }}">
                        <input type="hidden" name="activation_id" value="{{ r.activation_id }}">
                        <input type="hidden" name="password" value="{{ admin_password }}">
                        <input type="submit" value="Autorizar">
                    </form>
                    {% else %}
                        N/D
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
    # Validação da senha administrativa
    admin_pass = request.form.get("password")
    if admin_pass != ADMIN_PASSWORD:
        return "<h1>Acesso não autorizado</h1>", 401

    # Verifica se o activation_id foi informado
    activation_id = request.form.get("activation_id")
    if not activation_id:
        return "<h1>Activation ID não informado</h1>", 400

    # Consulta o registro correspondente no Supabase
    res = supabase.table("activations").select("*").eq("activation_id", activation_id).execute()
    if not res.data:
        return "<h1>Registro não encontrado</h1>", 404

    registro = res.data[0]

    # Verifica se o HWID já foi registrado (indicando que a app já iniciou a ativação)
    if not registro.get("hwid"):
        return "<h1>Ativação não iniciada. HWID não registrado.</h1>", 400

    # Constrói a página HTML informando que a autorização foi realizada com sucesso
    response_html = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <title>Autorização Realizada</title>
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
            a.button {{
                background-color: #EA5656;
                color: #fff;
                padding: 10px 20px;
                text-decoration: none;
                border-radius: 4px;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Autorização realizada com sucesso!</h1>
            <p>Activation ID: <strong>{registro.get("activation_id")}</strong></p>
            <p>Chave: <strong>{registro.get("chave")}</strong></p>
            <p>HWID: <strong>{registro.get("hwid")}</strong></p>
            <p>Tipo: <strong>{registro.get("tipo")}</strong></p>
            <p>Data de Ativação: <strong>{registro.get("data_ativacao")}</strong></p>
            <p>A app pode agora criar o <code>license.json</code> com estes dados.</p>
            <a class="button" href="{url_for('auth_hwid')}?password={ADMIN_PASSWORD}">Voltar</a>
        </div>
    </body>
    </html>
    """
    return response_html

if __name__ == '__main__':
    app.run(host="0.0.0.0")
