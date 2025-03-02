#!/usr/bin/env python3
import os
import random
import string
import datetime
import hashlib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
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

# Adicione estas variáveis de ambiente no bloco de variáveis de ambiente
EMAIL_HOST = os.environ.get("EMAIL_HOST")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_FROM = os.environ.get("EMAIL_FROM", EMAIL_USER)

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

# Se alguma das variáveis de email não estiver definida, mostre um aviso
import smtplib
import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

def send_key_email(recipient_email, key, key_type, transaction_id):
    """
    Envia um email com a chave de ativação para o cliente.
    
    Args:
        recipient_email (str): Email do destinatário
        key (str): Chave de ativação
        key_type (str): Tipo da chave (Uso Único ou LifeTime)
        transaction_id (str): ID da transação
    
    Returns:
        bool: True se o email foi enviado com sucesso, False caso contrário
    """
    # Verificamos as variáveis de ambiente já definidas no início do arquivo
    if not all([EMAIL_HOST, EMAIL_USER, EMAIL_PASSWORD]):
        print("Erro: Configurações de email incompletas.")
        return False
    
    # Prepara o assunto do email
    subject = "Chave de Ativação - Compra Concluída ✅"
    
    # Data da compra formatada
    purchase_date = datetime.datetime.now().strftime('%d/%m/%Y %H:%M')
    current_year = datetime.datetime.now().year
    
    # Prepara o corpo do email em HTML com f-strings para inserção segura de variáveis
    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Sua Chave de Ativação</title>
        <style>
            /* Sistema de cores */
            :root {{
                --dark: #171717;
                --accent: #7642ee;
                --light: #f8f8f8;
                --gray: #9ca3af;
                --success: #0cce6b;
                --border: #e5e5e5;
            }}
            
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            
            body {{
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                line-height: 1.5;
                color: var(--dark);
                background-color: #ffffff;
                -webkit-font-smoothing: antialiased;
                -moz-osx-font-smoothing: grayscale;
            }}
            
            .wrapper {{
                max-width: 600px;
                margin: 0 auto;
                background-color: #ffffff;
            }}
            
            .email-container {{
                padding: 40px;
            }}
            
            .header {{
                text-align: center;
                margin-bottom: 40px;
                position: relative;
            }}
            
            .success-badge {{
                display: inline-block;
                background-color: var(--success);
                border-radius: 50%;
                width: 60px;
                height: 60px;
                margin-bottom: 20px;
                position: relative;
            }}
            
            .success-badge::after {{
                content: "✓";
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                color: white;
                font-size: 30px;
                font-weight: bold;
            }}
            
            .header h1 {{
                font-size: 24px;
                font-weight: 700;
                letter-spacing: -0.5px;
                margin-bottom: 10px;
            }}
            
            .header p {{
                color: var(--gray);
                font-size: 16px;
            }}
            
            .divider {{
                height: 1px;
                background-color: var(--border);
                margin: 30px 0;
            }}
            
            .section {{
                margin: 30px 0;
            }}
            
            .section-title {{
                font-size: 14px;
                text-transform: uppercase;
                letter-spacing: 1.5px;
                color: var(--gray);
                margin-bottom: 15px;
                font-weight: 600;
            }}
            
            .key-container {{
                border: 2px dashed var(--accent);
                border-radius: 6px;
                padding: 20px;
                text-align: center;
                position: relative;
                background-color: rgba(238, 66, 102, 0.05);
            }}
            
            .key {{
                font-family: 'Courier New', monospace;
                font-size: 20px;
                font-weight: 700;
                letter-spacing: 2px;
                color: var(--accent);
                word-break: break-all;
            }}
            
            .copy-hint {{
                position: absolute;
                top: 10px;
                right: 10px;
                font-size: 12px;
                color: var(--gray);
            }}
            
            .details {{
                display: grid;
                grid-template-columns: 1fr;
                gap: 15px;
                margin: 30px 0;
            }}
            
            .detail-item {{
                display: flex;
                flex-direction: column;
                background-color: var(--light);
                padding: 15px;
                border-radius: 6px;
            }}
            
            .detail-label {{
                font-size: 14px;
                color: var(--gray);
                margin-bottom: 5px;
            }}
            
            .detail-value {{
                font-weight: 600;
                color: var(--dark);
            }}
            
            .steps {{
                counter-reset: step;
                margin: 30px 0;
            }}
            
            .step {{
                position: relative;
                padding-left: 40px;
                margin-bottom: 25px;
                counter-increment: step;
            }}
            
            .step::before {{
                content: counter(step);
                position: absolute;
                left: 0;
                top: 0;
                width: 26px;
                height: 26px;
                background-color: var(--accent);
                color: white;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-weight: 600;
                font-size: 14px;
            }}
            
            .step h4 {{
                font-size: 16px;
                margin-bottom: 5px;
            }}
            
            .step p {{
                color: var(--gray);
                font-size: 14px;
            }}
            
            .cta-button {{
                display: block;
                background-color: var(--accent);
                color: white;
                text-decoration: none;
                padding: 15px 25px;
                border-radius: 6px;
                text-align: center;
                font-weight: 600;
                margin: 30px 0;
                transition: background-color 0.2s;
            }}
            
            .cta-button:hover {{
                background-color: #663cd9;
            }}
            
            .support-section {{
                text-align: center;
                margin: 40px 0;
            }}
            
            .support-section p {{
                margin-bottom: 20px;
                color: var(--gray);
            }}
            
            .support-options {{
                display: flex;
                justify-content: center;
                gap: 20px;
                margin-top: 20px;
            }}
            
            .support-option {{
                display: inline-block;
                padding: 15px 20px;
                border: 1px solid var(--border);
                border-radius: 6px;
                text-decoration: none;
                color: var(--dark);
                font-weight: 500;
                transition: all 0.2s;
            }}
            
            .support-option:hover {{
                border-color: var(--accent);
                color: var(--accent);
            }}
            
            .footer {{
                text-align: center;
                color: var(--gray);
                font-size: 12px;
                margin-top: 50px;
                padding-top: 30px;
                border-top: 1px solid var(--border);
            }}
            
            .footer p {{
                margin-bottom: 10px;
            }}
            
            @media only screen and (max-width: 480px) {{
                .email-container {{
                    padding: 25px;
                }}
                
                .support-options {{
                    flex-direction: column;
                    gap: 10px;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="wrapper">
            <div class="email-container">
                <div class="header">
                    <div class="success-badge"></div>
                    <h1>Compra Concluída com Sucesso</h1>
                    <p>Obrigado pela preferência! Aqui está sua chave de ativação.</p>
                </div>
                
                <div class="divider"></div>
                
                <div class="section">
                    <h3 class="section-title">Sua Chave de Ativação</h3>
                    <div class="key-container">
                        <span class="copy-hint">Copie esta chave</span>
                        <div class="key">{key}</div>
                    </div>
                </div>
                
                <div class="section">
                    <h3 class="section-title">Detalhes da Compra</h3>
                    <div class="details">
                        <div class="detail-item">
                            <span class="detail-label">Tipo de Licença</span>
                            <span class="detail-value">{key_type}</span>
                        </div>
                        <div class="detail-item">
                            <span class="detail-label">ID da Transação</span>
                            <span class="detail-value">{transaction_id}</span>
                        </div>
                        <div class="detail-item">
                            <span class="detail-label">Data da Compra</span>
                            <span class="detail-value">{purchase_date}</span>
                        </div>
                    </div>
                </div>
                
                <div class="divider"></div>
                
                <div class="section">
                    <h3 class="section-title">Como Ativar</h3>
                    <div class="steps">
                        <div class="step">
                            <h4>Abra a aplicação</h4>
                            <p>Inicie a AstraKey que você acabou de instalar</p>
                        </div>
                        <div class="step">
                            <h4>Acesse a área de ativação</h4>
                            <p>Normalmente encontrada em "Configurações" ou na primeira execução</p>
                        </div>
                        <div class="step">
                            <h4>Insira sua chave</h4>
                            <p>Cole a chave exatamente como mostrada acima</p>
                        </div>
                        <div class="step">
                            <h4>Complete a ativação</h4>
                            <p>Clique em "Ativar" ou "Confirmar" para finalizar</p>
                        </div>
                    </div>
                </div>
                
                <a href="#" class="cta-button">Baixar AstraKey</a>
                
                <div class="divider"></div>
                
                <div class="support-section">
                    <h3 class="section-title">Precisa de ajuda?</h3>
                    <p>Se você encontrar qualquer problema durante a ativação, nossa equipe de suporte está pronta para ajudar.</p>
                    
                    <div class="support-options">
                        <a href="#" class="support-option">Centro de Suporte</a>
                    </div>
                </div>
                
                <div class="footer">
                    <p>Este email foi enviado para {recipient_email}</p>
                    <p>Este é um email automático. Por favor, não responda.</p>
                    <p>© {current_year} AstraKey. Todos os direitos reservados.</p>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    # Prepara a mensagem
    msg = MIMEMultipart()
    msg['From'] = EMAIL_FROM
    msg['To'] = recipient_email
    msg['Subject'] = subject
    
    # Anexa o corpo do email em HTML
    msg.attach(MIMEText(html_content, 'html'))
    
    try:
        # Configura a conexão SMTP
        server = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT)
        server.starttls()  # Ativa a criptografia TLS
        server.login(EMAIL_USER, EMAIL_PASSWORD)
        
        # Envia o email
        server.send_message(msg)
        server.quit()
        print(f"Email enviado com sucesso para {recipient_email}")
        return True
    except Exception as e:
        print(f"Erro ao enviar email: {str(e)}")
        return False

# Modifique a função stripe_webhook para incluir o envio de email
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
        customer_email = session.get("customer_details", {}).get("email", "")
        # Armazena os dados da sessão para a página de sucesso
        session_keys[session_id] = {
            "chave": chave, 
            "id_compra": session.get("id", "N/D"),
            "email": customer_email,
            "email_sent": False  # Inicialmente marcado como não enviado
        }
        compra = {
            "comprador": customer_email,
            "tipo_chave": tipo,
            "chave": chave,
            "id_compra": session.get("id", "N/D"),
            "preco": session.get("amount_total", "N/D"),
            "checkout_url": checkout_link
        }
        pending_buys.append(compra)
        
        # Envia o email com a chave
        email_sent = False
        if customer_email:
            email_sent = send_key_email(
                recipient_email=customer_email,
                key=chave,
                key_type=tipo,
                transaction_id=session.get("id", "N/D")
            )
            # Atualiza o status de envio do email
            session_keys[session_id]["email_sent"] = email_sent
        
        return jsonify({
            "status": "success", 
            "session_id": session_id, 
            "chave": chave,
            "email_sent": email_sent
        }), 200
    return jsonify({"status": "ignored"}), 200

@app.route("/sucesso", methods=["GET"])
def sucesso():
    session_id = request.args.get("session_id")
    if not session_id:
        return "<h1>Error:</h1><p>session_id is required.</p>", 400
    data = session_keys.get(session_id)
    if not data:
        return "<h1>Error:</h1><p>Key not found for the provided session.</p>", 404
    
    id_compra = data["id_compra"]
    email = data["email"]
    email_sent = data.get("email_sent", False)
    
    html = f"""
    <!DOCTYPE html>
    <html lang="pt">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Confirmação de Compra</title>
        <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;600;700&display=swap" rel="stylesheet">
        <style>
            :root {{
                --primary: #bfa560;
                --dark: #1c1b1b;
                --darker: #141414;
                --light: #f5e7c8;
                --success: #4caf50;
                --warning: #ff9800;
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
                animation: pulse 1.5s infinite;
            }}
            
            .warning-icon {{
                color: var(--warning);
                font-size: 3rem;
                margin-bottom: 1rem;
                animation: pulse 1.5s infinite;
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
            
            .email-highlight {{
                background-color: rgba(191, 165, 96, 0.2);
                border-radius: 4px;
                padding: 0.3rem 0.7rem;
                font-weight: 600;
                color: var(--primary);
                display: inline-block;
                margin: 0.5rem 0;
            }}
            
            .steps-container {{
                margin: 2rem 0;
                text-align: left;
                border-top: 1px solid rgba(191, 165, 96, 0.3);
                padding-top: 1.5rem;
            }}
            
            .steps-title {{
                font-size: 1.2rem;
                margin-bottom: 1rem;
                color: var(--primary);
                text-align: center;
            }}
            
            .step {{
                display: flex;
                align-items: flex-start;
                margin-bottom: 1.5rem;
            }}
            
            .step-number {{
                background-color: var(--primary);
                color: var(--darker);
                width: 30px;
                height: 30px;
                border-radius: 50%;
                display: flex;
                justify-content: center;
                align-items: center;
                font-weight: 700;
                margin-right: 1rem;
                flex-shrink: 0;
            }}
            
            .step-content {{
                flex-grow: 1;
            }}
            
            .step-title {{
                font-weight: 600;
                margin-bottom: 0.3rem;
                color: var(--primary);
            }}
            
            .envelope-icon {{
                font-size: 2.5rem;
                margin: 1rem 0;
                animation: float 3s ease-in-out infinite;
            }}
            
            .notice-box {{
                background-color: rgba(255, 152, 0, 0.1);
                border-left: 4px solid var(--warning);
                padding: 1rem;
                margin: 1.5rem 0;
                text-align: left;
                border-radius: 4px;
            }}
            
            footer {{
                margin-top: 2rem;
                font-size: 0.85rem;
                opacity: 0.7;
            }}
            
            @keyframes fadeIn {{
                from {{ opacity: 0; transform: translateY(20px); }}
                to {{ opacity: 1; transform: translateY(0); }}
            }}
            
            @keyframes pulse {{
                0% {{ transform: scale(1); opacity: 1; }}
                50% {{ transform: scale(1.1); opacity: 0.8; }}
                100% {{ transform: scale(1); opacity: 1; }}
            }}
            
            @keyframes float {{
                0% {{ transform: translateY(0px); }}
                50% {{ transform: translateY(-10px); }}
                100% {{ transform: translateY(0px); }}
            }}
            
            @media (max-width: 640px) {{
                .card {{
                    padding: 1.5rem;
                }}
                
                h1 {{
                    font-size: 1.5rem;
                }}
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                {'<div class="success-icon">✓</div>' if email_sent else '<div class="warning-icon">⚠️</div>'}
                <h1>Compra Concluída com Sucesso!</h1>
                
                <p>Obrigado pela sua compra. {"Sua chave de ativação foi enviada para:" if email_sent else "Tentamos enviar sua chave de ativação para:"}</p>
                
                <div class="email-highlight">{email}</div>
                
                <div class="envelope-icon">✉️</div>
                
                <p>{"Verifique sua caixa de entrada (e a pasta de spam) nos próximos minutos." if email_sent else "Houve um problema ao enviar o email. Entre em contato com nosso suporte para obter ajuda."}</p>
                
                {'''
                <div class="notice-box">
                    <p><strong>Aviso:</strong> Não foi possível enviar o email com sua chave. 
                    Por favor, entre em contato com nosso suporte através de <strong>suporte@seudominio.com</strong> 
                    e informe o ID de transação mostrado abaixo.</p>
                </div>
                ''' if not email_sent else ''}
                
                <div class="steps-container">
                    <h2 class="steps-title">Próximos Passos:</h2>
                    
                    <div class="step">
                        <div class="step-number">1</div>
                        <div class="step-content">
                            <div class="step-title">Verifique seu e-mail</div>
                            <p>Abra o e-mail que contém sua chave de ativação.</p>
                        </div>
                    </div>
                    
                    <div class="step">
                        <div class="step-number">2</div>
                        <div class="step-content">
                            <div class="step-title">Copie sua chave</div>
                            <p>Selecione e copie a chave de ativação do e-mail.</p>
                        </div>
                    </div>
                    
                    <div class="step">
                        <div class="step-number">3</div>
                        <div class="step-content">
                            <div class="step-title">Ative seu produto</div>
                            <p>Abra o aplicativo e cole sua chave no campo de ativação.</p>
                        </div>
                    </div>
                </div>
                
                <footer>
                    ID da transação: {id_compra}
                </footer>
            </div>
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
    
    # Retornar a nova chave em HTML - IMPORTANTE: Observe como o CSS está dentro de uma string Python
    response_html = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Sistema de Autorização</title>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        <style>
            :root {{
                --primary: #4f46e5;
                --primary-hover: #4338ca;
                --background: #0f172a;
                --card-bg: #1e293b;
                --text: #f8fafc;
                --text-secondary: #94a3b8;
                --success: #10b981;
                --error: #ef4444;
                --warning: #f59e0b;
            }}

            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
                transition: all 0.3s ease;
            }}

            body {{
                background: var(--background);
                color: var(--text);
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                line-height: 1.6;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                padding: 20px;
            }}

            .container {{
                width: 100%;
                max-width: 600px;
                animation: fadeIn 0.5s ease-in-out;
            }}

            .card {{
                background: var(--card-bg);
                border-radius: 16px;
                padding: 30px;
                box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3);
                margin-bottom: 20px;
                overflow: hidden;
                position: relative;
            }}

            .card::before {{
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 5px;
                background: linear-gradient(90deg, var(--primary), var(--primary-hover));
            }}

            h1 {{
                font-size: 2rem;
                margin-bottom: 1.5rem;
                color: var(--text);
                text-align: center;
                font-weight: 700;
            }}

            h2 {{
                font-size: 1.5rem;
                margin-bottom: 1rem;
                color: var(--text);
                font-weight: 600;
            }}

            p {{
                color: var(--text-secondary);
                margin-bottom: 1.5rem;
                font-size: 1rem;
            }}

            .key-box {{
                background: rgba(15, 23, 42, 0.7);
                border-radius: 8px;
                padding: 20px;
                margin: 20px 0;
                border: 1px solid rgba(79, 70, 229, 0.3);
                position: relative;
                overflow: hidden;
            }}

            .key-value {{
                font-family: 'Courier New', monospace;
                font-size: 1.2rem;
                letter-spacing: 1px;
                word-break: break-all;
                color: var(--text);
                text-align: center;
                margin: 10px 0;
            }}

            .copy-btn {{
                background: var(--primary);
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 20px;
                font-size: 1rem;
                cursor: pointer;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 8px;
                margin: 0 auto;
                transition: transform 0.2s ease, background 0.3s ease;
            }}

            .copy-btn:hover {{
                background: var(--primary-hover);
                transform: translateY(-2px);
            }}

            .copy-btn:active {{
                transform: translateY(0);
            }}

            .status {{
                margin-top: 20px;
                padding: 15px;
                border-radius: 8px;
                font-weight: 500;
                text-align: center;
                display: flex;
                align-items: center;
                justify-content: center;
                gap: 10px;
            }}

            .status.success {{
                background: rgba(16, 185, 129, 0.2);
                color: var(--success);
            }}

            .status.error {{
                background: rgba(239, 68, 68, 0.2);
                color: var(--error);
            }}

            .icon-pulse {{
                animation: pulse 2s infinite;
            }}

            @keyframes pulse {{
                0% {{
                    transform: scale(1);
                }}
                50% {{
                    transform: scale(1.1);
                }}
                100% {{
                    transform: scale(1);
                }}
            }}

            @keyframes fadeIn {{
                from {{
                    opacity: 0;
                    transform: translateY(20px);
                }}
                to {{
                    opacity: 1;
                    transform: translateY(0);
                }}
            }}

            @keyframes slideIn {{
                from {{
                    transform: translateX(-100%);
                }}
                to {{
                    transform: translateX(0);
                }}
            }}

            .steps {{
                margin: 30px 0;
            }}

            .step {{
                display: flex;
                margin-bottom: 15px;
                opacity: 0;
                animation: fadeIn 0.5s ease forwards;
            }}

            .step:nth-child(1) {{ animation-delay: 0.2s; }}
            .step:nth-child(2) {{ animation-delay: 0.4s; }}
            .step:nth-child(3) {{ animation-delay: 0.6s; }}

            .step-number {{
                background: var(--primary);
                color: white;
                width: 30px;
                height: 30px;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                margin-right: 15px;
                flex-shrink: 0;
            }}

            .step-content {{
                flex: 1;
            }}

            .loading {{
                display: inline-block;
                width: 20px;
                height: 20px;
                border: 3px solid rgba(255,255,255,0.3);
                border-radius: 50%;
                border-top-color: white;
                animation: spin 1s ease-in-out infinite;
                margin-right: 10px;
            }}

            @keyframes spin {{
                to {{ transform: rotate(360deg); }}
            }}

            .hidden {{
                display: none;
            }}

            footer {{
                text-align: center;
                margin-top: 30px;
                color: var(--text-secondary);
                font-size: 0.875rem;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <h1><i class="fas fa-shield-alt"></i> Autorização Atualizada</h1>
                
                <div class="status success">
                    <i class="fas fa-check-circle icon-pulse"></i>
                    <span>Processo concluído com sucesso</span>
                </div>
                
                <div style="margin-top: 30px;">
                    <h2>Nova chave de ativação</h2>
                    <p>Sua licença anterior foi revogada. Utilize a nova chave abaixo para reativar sua aplicação.</p>
                    
                    <div class="key-box">
                        <div class="key-value" id="key-value">{new_key}</div>
                        <button class="copy-btn" id="copy-btn">
                            <i class="fas fa-copy"></i> Copiar Chave
                        </button>
                    </div>
                </div>

                <div class="steps">
                    <h2>Próximos passos:</h2>
                    <div class="step">
                        <div class="step-number">1</div>
                        <div class="step-content">
                            <strong>Copie a chave</strong>
                            <p>Use o botão acima para copiar sua nova chave de ativação</p>
                        </div>
                    </div>
                    <div class="step">
                        <div class="step-number">2</div>
                        <div class="step-content">
                            <strong>Feche a aplicação</strong>
                            <p>Certifique-se de fechar completamente o programa</p>
                        </div>
                    </div>
                    <div class="step">
                        <div class="step-number">3</div>
                        <div class="step-content">
                            <strong>Reinicie e ative</strong>
                            <p>Abra novamente a aplicação e use a nova chave para ativar</p>
                        </div>
                    </div>
                </div>
            </div>
            
            <footer>
                <p>© 2025 Sistema de Autorização • Todos os direitos reservados</p>
            </footer>
        </div>

        <script>
            // Função para copiar a chave
            document.getElementById('copy-btn').addEventListener('click', function() {{
                const keyText = document.getElementById('key-value').innerText;
                navigator.clipboard.writeText(keyText).then(function() {{
                    const btn = document.getElementById('copy-btn');
                    const originalText = btn.innerHTML;
                    
                    btn.innerHTML = '<i class="fas fa-check"></i> Copiado!';
                    btn.style.background = 'var(--success)';
                    
                    setTimeout(function() {{
                        btn.innerHTML = originalText;
                        btn.style.background = 'var(--primary)';
                    }}, 2000);
                }}).catch(function(err) {{
                    console.error('Erro ao copiar: ', err);
                    alert('Não foi possível copiar automaticamente. Por favor, selecione e copie manualmente.');
                }});
            }});

            // Animação de entrada
            document.addEventListener('DOMContentLoaded', function() {{
                const container = document.querySelector('.container');
                container.style.opacity = '0';
                
                setTimeout(function() {{
                    container.style.opacity = '1';
                    container.style.transform = 'translateY(0)';
                }}, 100);
            }});
        </script>
    </body>
    </html>
    """
    return response_html, 200

if __name__ == '__main__':
    app.run(host="0.0.0.0")
