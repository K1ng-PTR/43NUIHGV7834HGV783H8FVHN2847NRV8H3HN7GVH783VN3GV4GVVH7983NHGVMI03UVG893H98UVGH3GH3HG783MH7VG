#!/usr/bin/env python3
import os
import random
import string
import datetime
import hashlib
from datetime import timedelta
from dotenv import load_dotenv
import stripe
from supabase import create_client, Client

from fastapi import FastAPI, Request, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

load_dotenv()

# Inicializa o FastAPI
app = FastAPI()

# Configura templates Jinja2 (usado para os endpoints HTML)
templates = Jinja2Templates(directory="templates")

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

# ============================
# ENDPOINTS DA API (JSON)
# ============================

@app.post("/gerar/{quantidade}")
async def gerar_multiplo(quantidade: int, request: Request):
    if quantidade < 1 or quantidade > 300:
        raise HTTPException(status_code=400, detail="Quantidade deve ser entre 1 e 300.")
    provided_password = request.headers.get("X-Gen-Password", "")
    if provided_password != SUPER_PASSWORD:
        raise HTTPException(status_code=401, detail="Acesso não autorizado")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido.")
    if not data or 'tipo' not in data:
        raise HTTPException(status_code=400, detail="O campo 'tipo' é obrigatório.")
    tipo = data.get("tipo")
    if tipo not in ["Uso Único", "LifeTime"]:
        raise HTTPException(status_code=400, detail="Tipo inválido. Deve ser 'Uso Único' ou 'LifeTime'.")

    chaves_geradas = []
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
            raise HTTPException(status_code=500, detail=f"Erro ao inserir registro no banco: {res.error.message}")
        chaves_geradas.append({
            "chave": chave,
            "tipo": tipo,
            "activation_id": activation_id,
            "data_ativacao": None
        })
    return {"chaves": chaves_geradas}


@app.post("/validation")
async def validate(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido.")
    if not data or 'chave' not in data or 'hwid' not in data:
        raise HTTPException(status_code=400, detail="Os campos 'chave' e 'hwid' são obrigatórios.")

    chave = data.get("chave")
    hwid_request = data.get("hwid")

    try:
        res = supabase.table("activations").select("*").eq("chave", chave).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao consultar o banco: {str(e)}")

    if not res.data:
        return {"valid": False, "message": "Chave inválida."}

    registro = res.data[0]

    # Se a licença foi revogada, instrui a app a apagar o license.json e reabrir o menu de ativação
    if registro.get("revoked"):
        return {
            "valid": False,
            "reset": True,
            "message": "Licença revogada. Por favor, apague license.json e reative a chave."
        }

    # Se o registro já foi ativado (ou seja, já possui um HWID registrado)
    if registro.get("hwid"):
        if registro.get("hwid") != hwid_request:
            return {
                "valid": False,
                "message": "Autorização Recusada"
            }
        expected_activation_id = generate_activation_id(hwid_request, chave)
        if registro.get("activation_id") != expected_activation_id:
            # A API informa que a licença foi atualizada (ex.: nova chave gerada pelo admin)
            return {
                "valid": False,
                "update": True,
                "new_data": registro,
                "message": "Nova chave gerada. A licença será atualizada."
            }
        # Para chaves do tipo "Uso Único", verifica expiração
        if registro.get("tipo") == "Uso Único":
            try:
                activation_date = datetime.datetime.fromisoformat(registro.get("data_ativacao"))
            except Exception as e:
                raise HTTPException(status_code=400, detail="Data de ativação inválida.")
            expiration_date = activation_date + timedelta(days=1)
            if datetime.datetime.now() > expiration_date:
                return {"valid": False, "message": "Chave expirada."}
        return {
            "valid": True,
            "tipo": registro.get("tipo"),
            "data_ativacao": registro.get("data_ativacao"),
            "activation_id": registro.get("activation_id"),
            "message": "Chave validada com sucesso."
        }

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
            raise HTTPException(status_code=500, detail="Erro ao atualizar registro: Dados não retornados")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar registro: {str(e)}")
        
    registro.update(update_data)
    return {
        "valid": True,
        "tipo": registro.get("tipo"),
        "data_ativacao": now_dt,
        "activation_id": new_activation_id,
        "message": "Chave validada com sucesso."
    }


@app.get("/buys")
async def get_buys():
    global pending_buys
    compras = pending_buys.copy()
    pending_buys.clear()
    return compras


@app.get("/ping")
async def ping():
    return {"status": "alive"}


@app.api_route("/", methods=["GET", "POST", "HEAD"])
async def index():
    return {"message": "API de chaves rodando."}


@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload_bytes = await request.body()
    payload = payload_bytes.decode("utf-8")
    sig_header = request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError as e:
        return JSONResponse(status_code=400, content={"error": "Assinatura inválida"})
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

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
            "hwid": "",
            "chave": chave,
            "activation_id": activation_id,
            "data_ativacao": now_dt,
            "tipo": tipo
        }
        try:
            res = supabase.table("activations").insert(registro).execute()
        except Exception as e:
            return JSONResponse(status_code=500, content={"error": "Erro ao inserir registro via Stripe", "details": str(e)})

        if not res.data:
            return JSONResponse(status_code=500, content={"error": "Erro ao inserir registro via Stripe", "details": "Dados não retornados"})

        session_id = session.get("id")
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
        return {"status": "success", "session_id": session_id, "chave": chave}
    return {"status": "ignored"}


@app.get("/sucesso", response_class=HTMLResponse)
async def sucesso(session_id: str = Query(None)):
    if not session_id:
        return HTMLResponse(content="<h1>Erro:</h1><p>session_id é necessário.</p>", status_code=400)
    data = session_keys.get(session_id)
    if not data:
        return HTMLResponse(content="<h1>Erro:</h1><p>Chave não encontrada para a sessão fornecida.</p>", status_code=404)
    chave = data["chave"]
    id_compra = data["id_compra"]
    res = supabase.table("activations").select("*").eq("chave", chave).execute()
    if not res.data:
        return HTMLResponse(content="<h1>Erro:</h1><p>Detalhes da chave não encontrados.</p>", status_code=404)
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
    return HTMLResponse(content=html)

# ==================================
# ENDPOINTS ADMINISTRATIVOS (/auth-hwid)
# ==================================

# Template HTML (utilizando sintaxe do Jinja2)
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
            <form method="post" action="/auth-hwid">
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
                    <form method="post" action="/auth-hwid/authorize">
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

@app.post("/auth-hwid", response_class=HTMLResponse)
@app.get("/auth-hwid", response_class=HTMLResponse)
async def auth_hwid(request: Request, password: str = Query(None)):
    authenticated = False
    admin_pass = None
    if request.method == "POST":
        form = await request.form()
        admin_pass = form.get("password")
        if admin_pass == ADMIN_PASSWORD:
            authenticated = True
        else:
            return templates.TemplateResponse(
                "auth_hwid.html",
                {"request": request, "authenticated": False},
                status_code=401
            )
    else:
        admin_pass = password
        if admin_pass == ADMIN_PASSWORD:
            authenticated = True
    if not authenticated:
        return templates.TemplateResponse(
            "auth_hwid.html",
            {"request": request, "authenticated": False}
        )
    result = supabase.table("activations").select("*").execute()
    records = result.data if result.data else []
    return templates.TemplateResponse(
        "auth_hwid.html",
        {"request": request, "authenticated": True, "records": records, "admin_password": ADMIN_PASSWORD}
    )

@app.post("/auth-hwid/authorize", response_class=HTMLResponse)
async def auth_hwid_authorize(request: Request):
    # Tenta obter dados do formulário ou JSON
    form = await request.form()
    admin_pass = form.get("password")
    if not admin_pass:
        try:
            data = await request.json()
            admin_pass = data.get("password")
        except Exception:
            pass
    if admin_pass != ADMIN_PASSWORD:
        return HTMLResponse(content="<h1>Acesso não autorizado</h1>", status_code=401)
    activation_id_old = form.get("activation_id")
    if not activation_id_old:
        try:
            data = await request.json()
            activation_id_old = data.get("activation_id")
        except Exception:
            pass
    if not activation_id_old:
        return HTMLResponse(content="<h1>Activation ID não informado</h1>", status_code=400)
    res = supabase.table("activations").select("*").eq("activation_id", activation_id_old).execute()
    if not res.data:
        return HTMLResponse(content="<h1>Registro não encontrado</h1>", status_code=404)
    # Marcar o registro antigo como revogado
    revoke_update = {"revoked": True}
    supabase.table("activations").update(revoke_update).eq("activation_id", activation_id_old).execute()
    # Gerar nova chave do tipo LifeTime
    new_key = generate_key()
    new_activation_id = generate_activation_id("", new_key)  # sem HWID
    new_record = {
        "hwid": "",
        "chave": new_key,
        "activation_id": new_activation_id,
        "data_ativacao": None,
        "tipo": "LifeTime",
        "revoked": False
    }
    insert_res = supabase.table("activations").insert(new_record).execute()
    if not insert_res.data:
        return HTMLResponse(content=f"<h1>Erro ao inserir novo registro: {insert_res}</h1>", status_code=500)
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
    return HTMLResponse(content=response_html, status_code=200)

# Caso queira executar via comando "python nome_do_arquivo.py"
if __name__ == '__main__':
    import uvicorn
    uvicorn.run("main:API", host="0.0.0.0", port=8000, reload=True)
