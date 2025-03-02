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
    html = f"""
    <!DOCTYPE html>
    <html lang="pt">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Sua Chave de Ativação</title>
        <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600;700&display=swap" rel="stylesheet">
        <style>
            :root {{
                --primary: #bfa560;
                --dark: #1c1b1b;
                --darker: #141414;
                --light: #f5e7c8;
                --success: #4caf50;
                --shadow: rgba(191, 165, 96, 0.25);
            }}
            
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            
            body {{
                background: linear-gradient(135deg, var(--darker) 0%, var(--dark) 100%);
                min-height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
                font-family: 'Poppins', sans-serif;
                color: var(--light);
                padding: 20px;
            }}
            
            .container {{
                width: 100%;
                max-width: 600px;
                animation: fadeIn 0.6s ease-out;
            }}
            
            .card {{
                background-color: rgba(43, 43, 43, 0.8);
                backdrop-filter: blur(10px);
                border: 2px solid var(--primary);
                border-radius: 16px;
                box-shadow: 0 8px 32px var(--shadow);
                padding: 2.5rem;
                text-align: center;
            }}
            
            .success-icon {{
                color: var(--success);
                font-size: 3rem;
                margin-bottom: 1rem;
            }}
            
            h1 {{
                font-size: 1.8rem;
                font-weight: 700;
                margin-bottom: 1.5rem;
                color: var(--primary);
            }}
            
            p {{
                margin-bottom: 1.5rem;
                font-size: 1rem;
                opacity: 0.9;
                line-height: 1.6;
            }}
            
            .key-container {{
                position: relative;
                margin: 2rem 0;
            }}
            
            .key-display {{
                background-color: rgba(0, 0, 0, 0.3);
                border: 1px solid var(--primary);
                border-radius: 12px;
                padding: 1rem;
                font-family: monospace;
                font-size: 1.4rem;
                letter-spacing: 2px;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
                user-select: all;
                transition: all 0.3s ease;
                cursor: pointer;
            }}
            
            .key-display:hover {{
                background-color: rgba(0, 0, 0, 0.5);
                transform: translateY(-2px);
                box-shadow: 0 5px 15px var(--shadow);
            }}
            
            .btn {{
                display: inline-block;
                background-color: var(--primary);
                color: var(--dark);
                border: none;
                padding: 0.8rem 1.5rem;
                font-size: 1rem;
                font-weight: 600;
                border-radius: 8px;
                cursor: pointer;
                transition: all 0.3s ease;
                box-shadow: 0 4px 6px var(--shadow);
            }}
            
            .btn:hover {{
                transform: translateY(-2px);
                box-shadow: 0 7px 10px var(--shadow);
            }}
            
            .btn:active {{
                transform: translateY(1px);
            }}
            
            .copy-notification {{
                position: fixed;
                top: 20px;
                left: 50%;
                transform: translateX(-50%);
                background-color: var(--success);
                color: white;
                padding: 10px 20px;
                border-radius: 8px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
                opacity: 0;
                transition: opacity 0.3s ease;
                z-index: 1000;
            }}
            
            .copy-notification.show {{
                opacity: 1;
            }}
            
            .instructions {{
                border-top: 1px solid rgba(191, 165, 96, 0.3);
                margin-top: 2rem;
                padding-top: 1.5rem;
                text-align: left;
            }}
            
            .instructions h2 {{
                font-size: 1.2rem;
                margin-bottom: 1rem;
                color: var(--primary);
            }}
            
            .instructions ol {{
                margin-left: 1.5rem;
                margin-bottom: 1.5rem;
            }}
            
            .instructions li {{
                margin-bottom: 0.5rem;
            }}
            
            footer {{
                margin-top: 2rem;
                font-size: 0.9rem;
                opacity: 0.7;
            }}
            
            @keyframes fadeIn {{
                from {{ opacity: 0; transform: translateY(20px); }}
                to {{ opacity: 1; transform: translateY(0); }}
            }}
            
            @media (max-width: 640px) {{
                .card {{
                    padding: 1.5rem;
                }}
                
                h1 {{
                    font-size: 1.5rem;
                }}
                
                .key-display {{
                    font-size: 1rem;
                    padding: 0.8rem;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <div class="success-icon">✓</div>
                <h1>Compra Concluída com Sucesso!</h1>
                <p>Obrigado pela sua compra. Sua chave de ativação está pronta para uso.</p>
                
                <div class="key-container">
                    <div class="key-display" id="chave" onclick="copyKey()">{chave}</div>
                </div>
                
                <button class="btn" onclick="copyKey()">Copiar Chave</button>
                
                <div class="instructions">
                    <h2>Como utilizar sua chave:</h2>
                    <ol>
                        <li>Abra o aplicativo que você deseja ativar</li>
                        <li>Navegue até a tela de ativação</li>
                        <li>Cole a chave no campo indicado</li>
                        <li>Clique em ativar para completar o processo</li>
                    </ol>
                </div>
                
                <footer>
                    ID da transação: {id_compra}
                </footer>
            </div>
        </div>
        
        <div class="copy-notification" id="notification">
            Chave copiada com sucesso!
        </div>
        
        <script>
            function copyKey() {{
                const keyText = "{chave}";
                navigator.clipboard.writeText(keyText)
                    .then(() => {{
                        const notification = document.getElementById('notification');
                        notification.classList.add('show');
                        
                        // Highlight effect on the key
                        const keyDisplay = document.getElementById('chave');
                        keyDisplay.style.backgroundColor = 'rgba(76, 175, 80, 0.2)';
                        
                        setTimeout(() => {{
                            notification.classList.remove('show');
                            keyDisplay.style.backgroundColor = '';
                        }}, 2000);
                    }})
                    .catch(err => {{
                        console.error('Erro ao copiar: ', err);
                        alert('Não foi possível copiar automaticamente. Por favor, selecione a chave manualmente e copie.');
                    }});
            }}
            
            // Allow copying by clicking anywhere on the key
            document.getElementById('chave').addEventListener('click', function(e) {{
                const range = document.createRange();
                range.selectNode(this);
                window.getSelection().removeAllRanges();
                window.getSelection().addRange(range);
                copyKey();
            }});
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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Administração - Auth HWID</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root {
            --primary: #bfa560;
            --primary-dark: #a08a47;
            --background: #1a1a1a;
            --surface: #2b2b2b;
            --surface-light: #3a3a3a;
            --text: #f5e7c8;
            --text-secondary: #d1c0a5;
            --danger: #e74c3c;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            transition: all 0.3s ease;
        }

        body {
            background: linear-gradient(135deg, var(--background) 0%, #252525 100%);
            color: var(--text);
            font-family: 'Segoe UI', Arial, sans-serif;
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }

        .container {
            width: 100%;
            max-width: 800px;
            background-color: var(--surface);
            border: 2px solid var(--primary);
            box-shadow: 0 0 20px rgba(191, 165, 96, 0.2);
            padding: 30px;
            border-radius: 12px;
        }

        .header {
            text-align: center;
            margin-bottom: 25px;
            position: relative;
        }

        .header h1, .header h2 {
            color: var(--primary);
            margin-bottom: 10px;
            letter-spacing: 1px;
        }

        .badge {
            position: absolute;
            top: -10px;
            right: -10px;
            background: var(--primary);
            color: var(--background);
            padding: 5px 10px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
        }

        form {
            margin: 20px 0;
        }

        .input-group {
            position: relative;
            margin-bottom: 20px;
        }

        .input-group i {
            position: absolute;
            left: 15px;
            top: 12px;
            color: var(--primary);
        }

        input[type="password"], input[type="text"] {
            width: 100%;
            padding: 12px 15px 12px 45px;
            border: 1px solid var(--surface-light);
            border-radius: 8px;
            background-color: var(--surface-light);
            color: var(--text);
            font-size: 16px;
        }

        input[type="password"]:focus, input[type="text"]:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 2px rgba(191, 165, 96, 0.3);
        }

        button, input[type="submit"] {
            width: 100%;
            padding: 12px 15px;
            border: none;
            border-radius: 8px;
            background-color: var(--primary);
            color: var(--background);
            cursor: pointer;
            font-weight: bold;
            font-size: 16px;
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 10px;
        }

        button:hover, input[type="submit"]:hover {
            background-color: var(--primary-dark);
            transform: translateY(-2px);
            box-shadow: 0 4px 8px rgba(0,0,0,0.2);
        }

        .table-container {
            overflow-x: auto;
            margin-top: 25px;
            border-radius: 8px;
            box-shadow: 0 4px 8px rgba(0,0,0,0.1);
        }

        table {
            width: 100%;
            border-collapse: collapse;
        }

        th, td {
            padding: 12px 15px;
            text-align: left;
            border-bottom: 1px solid var(--surface-light);
        }

        th {
            background-color: var(--surface-light);
            color: var(--primary);
            font-weight: bold;
            text-transform: uppercase;
            font-size: 0.85em;
            letter-spacing: 1px;
        }

        tr:last-child td {
            border-bottom: none;
        }

        tbody tr:hover {
            background-color: rgba(191, 165, 96, 0.1);
        }

        .auth-status {
            padding: 5px 10px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
            display: inline-block;
        }

        .authorized {
            background-color: rgba(46, 204, 113, 0.2);
            color: #2ecc71;
        }

        .unauthorized {
            background-color: rgba(231, 76, 60, 0.2);
            color: #e74c3c;
        }

        .action-btn {
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 14px;
            width: auto;
        }

        .logout-btn {
            position: absolute;
            top: 10px;
            right: 10px;
            background: transparent;
            border: 1px solid var(--primary);
            color: var(--primary);
            width: auto;
            padding: 5px 10px;
            font-size: 14px;
        }

        .logout-btn:hover {
            background: var(--primary);
            color: var(--background);
        }

        .search-box {
            margin-bottom: 20px;
        }

        @media (max-width: 768px) {
            .container {
                padding: 20px;
            }
            
            th, td {
                padding: 10px 8px;
                font-size: 14px;
            }
        }
        
        .hidden {
            display: none;
        }
        
        .pagination {
            display: flex;
            justify-content: center;
            margin-top: 20px;
            gap: 10px;
        }
        
        .pagination button {
            width: auto;
            padding: 8px 12px;
        }
        
        .toast {
            position: fixed;
            top: 20px;
            right: 20px;
            padding: 15px 20px;
            background: var(--primary);
            color: var(--background);
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.2);
            opacity: 0;
            transform: translateY(-20px);
        }
        
        .toast.show {
            opacity: 1;
            transform: translateY(0);
        }
    </style>
</head>
<body>
    <div class="container">
        {% if not authenticated %}
        <div id="login-box">
            <div class="header">
                <h2>Administração Auth HWID</h2>
                <div class="badge">Secure Access</div>
            </div>
            <form method="post" action="{{ url_for('auth_hwid') }}" id="login-form">
                <div class="input-group">
                    <i class="fas fa-lock"></i>
                    <input type="password" name="password" id="password" placeholder="Senha de Admin" required>
                </div>
                <button type="submit">
                    <i class="fas fa-sign-in-alt"></i>
                    Entrar
                </button>
            </form>
        </div>
        {% else %}
        <div class="header">
            <button class="logout-btn">
                <i class="fas fa-sign-out-alt"></i> Sair
            </button>
            <h1>Registros de Ativação</h1>
            <p>Gerencie autorizações de HWID</p>
        </div>
        
        <div class="search-box">
            <div class="input-group">
                <i class="fas fa-search"></i>
                <input type="text" id="searchInput" placeholder="Buscar por chave ou HWID...">
            </div>
        </div>
        
        <div class="table-container">
            <table id="records-table">
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>Chave</th>
                        <th>Tipo</th>
                        <th>HWID</th>
                        <th>Data Ativação</th>
                        <th>Status</th>
                        <th>Ação</th>
                    </tr>
                </thead>
                <tbody>
                    {% for r in records %}
                    <tr>
                        <td>{{ r.activation_id }}</td>
                        <td>{{ r.chave }}</td>
                        <td>
                            <span class="badge" style="background: {% if r.tipo == 'Premium' %}#9b59b6{% else %}#3498db{% endif %}">
                                {{ r.tipo }}
                            </span>
                        </td>
                        <td title="{{ r.hwid or 'Não definido' }}">{{ r.hwid or "N/D" }}</td>
                        <td>{{ r.data_ativacao or "N/D" }}</td>
                        <td>
                            {% if r.authorized %}
                            <span class="auth-status authorized">Autorizado</span>
                            {% else %}
                            <span class="auth-status unauthorized">Pendente</span>
                            {% endif %}
                        </td>
                        <td>
                            {% if not r.authorized %}
                            <form method="post" action="{{ url_for('auth_hwid_authorize') }}" class="auth-form">
                                <input type="hidden" name="activation_id" value="{{ r.activation_id }}">
                                <input type="hidden" name="password" value="{{ admin_password }}">
                                <button type="submit" class="action-btn">
                                    <i class="fas fa-check"></i> Autorizar
                                </button>
                            </form>
                            {% else %}
                            <button class="action-btn" style="background-color: var(--danger);" onclick="revokeAuth({{ r.activation_id }})">
                                <i class="fas fa-ban"></i> Revogar
                            </button>
                            {% endif %}
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        
        <div class="pagination">
            <button id="prevPage"><i class="fas fa-chevron-left"></i> Anterior</button>
            <button id="nextPage">Próximo <i class="fas fa-chevron-right"></i></button>
        </div>
        {% endif %}
    </div>
    
    <div id="toast" class="toast">Ação realizada com sucesso!</div>
    
    <script>
        document.addEventListener('DOMContentLoaded', function() {
            // Login animation
            const loginForm = document.getElementById('login-form');
            if (loginForm) {
                loginForm.addEventListener('submit', function(e) {
                    const submitBtn = this.querySelector('button[type="submit"]');
                    submitBtn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Entrando...';
                });
            }
            
            // Search functionality
            const searchInput = document.getElementById('searchInput');
            if (searchInput) {
                searchInput.addEventListener('input', function() {
                    const searchTerm = this.value.toLowerCase();
                    const rows = document.querySelectorAll('#records-table tbody tr');
                    
                    rows.forEach(row => {
                        const chave = row.cells[1].textContent.toLowerCase();
                        const hwid = row.cells[3].textContent.toLowerCase();
                        
                        if (chave.includes(searchTerm) || hwid.includes(searchTerm)) {
                            row.style.display = '';
                        } else {
                            row.style.display = 'none';
                        }
                    });
                });
            }
            
            // Pagination
            const table = document.getElementById('records-table');
            if (table) {
                const rowsPerPage = 5;
                const rows = table.querySelectorAll('tbody tr');
                const pageCount = Math.ceil(rows.length / rowsPerPage);
                let currentPage = 1;
                
                function showPage(page) {
                    const start = (page - 1) * rowsPerPage;
                    const end = start + rowsPerPage;
                    
                    rows.forEach((row, index) => {
                        row.style.display = (index >= start && index < end) ? '' : 'none';
                    });
                }
                
                showPage(currentPage);
                
                document.getElementById('prevPage').addEventListener('click', function() {
                    if (currentPage > 1) {
                        currentPage--;
                        showPage(currentPage);
                    }
                });
                
                document.getElementById('nextPage').addEventListener('click', function() {
                    if (currentPage < pageCount) {
                        currentPage++;
                        showPage(currentPage);
                    }
                });
            }
            
            // Form submissions with toast notification
            const authForms = document.querySelectorAll('.auth-form');
            authForms.forEach(form => {
                form.addEventListener('submit', function(e) {
                    e.preventDefault();
                    const submitBtn = this.querySelector('button[type="submit"]');
                    const originalHTML = submitBtn.innerHTML;
                    submitBtn.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i>';
                    submitBtn.disabled = true;
                    
                    setTimeout(() => {
                        this.submit();
                        showToast('Autorização concedida com sucesso!');
                    }, 500);
                });
            });
            
            // Toast notification
            function showToast(message) {
                const toast = document.getElementById('toast');
                toast.textContent = message;
                toast.classList.add('show');
                
                setTimeout(() => {
                    toast.classList.remove('show');
                }, 3000);
            }
            
            // HWID revoke function (example)
            window.revokeAuth = function(id) {
                if (confirm('Tem certeza que deseja revogar esta autorização?')) {
                    showToast('Autorização revogada com sucesso!');
                    // Here you would handle the revoke action with your backend
                }
            };
            
            // Logout button
            const logoutBtn = document.querySelector('.logout-btn');
            if (logoutBtn) {
                logoutBtn.addEventListener('click', function() {
                    if (confirm('Deseja realmente sair?')) {
                        window.location.href = "{{ url_for('auth_hwid') }}";  // Changed to use existing route
                    }
                });
            }
        });
    </script>
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

# Add this route for logout functionality
@app.route("/auth-hwid-logout")
def auth_hwid_logout():
    return redirect(url_for('auth_hwid'))

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
