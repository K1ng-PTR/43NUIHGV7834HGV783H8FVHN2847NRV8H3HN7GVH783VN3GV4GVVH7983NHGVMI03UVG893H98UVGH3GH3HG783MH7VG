import discord
import aiohttp
import asyncio
import threading
import os
from flask import Flask

# 🔧 Configurações do Bot
TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # Token do bot nas variáveis de ambiente
CHANNEL_ID = 1339675439987298334        # ID do canal (como int)
API_URL = "https://api-cjng.onrender.com/buys"  # Endpoint para buscar as compras

if not TOKEN:
    raise ValueError("⚠️ ERRO: O token do bot do Discord não foi definido! Verifique as variáveis de ambiente.")

# Configuração do Flask para status (se necessário)
app = Flask(__name__)

# Configuração do bot do Discord
intents = discord.Intents.default()
client = discord.Client(intents=intents)

async def fetch_buys():
    """Busca as compras pendentes na API."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(API_URL) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"⚠️ Erro ao buscar compras: {response.status}")
        except Exception as e:
            print(f"⚠️ Erro na requisição da API: {e}")
        return []

async def send_buys():
    """Envia as compras confirmadas para o canal do Discord."""
    await client.wait_until_ready()
    channel = client.get_channel(CHANNEL_ID)
    if not channel:
        print("❌ Canal do Discord não encontrado! Verifique o CHANNEL_ID.")
        return
    while not client.is_closed():
        buys = await fetch_buys()
        for buy in buys:
            embed = discord.Embed(
                title="🛒 Nova Compra Confirmada!", 
                color=discord.Color.green()
            )
            embed.add_field(name="👤 Comprador", value=buy.get("comprador", "N/A"), inline=False)
            embed.add_field(name="🔑 Tipo de Chave", value=buy.get("tipo_chave", "N/A"), inline=False)
            embed.add_field(name="🔐 Chave Gerada", value=buy.get("chave", "N/A"), inline=False)
            embed.add_field(name="💳 ID da compra", value=buy.get("id_compra", "N/A"), inline=False)
            embed.add_field(name="💲Preço", value=buy.get("preco", "N/A"), inline=False)
            await channel.send(embed=embed)
        await asyncio.sleep(30)  # Consulta a cada 30 segundos

@client.event
async def on_ready():
    print(f'✅ Bot conectado como {client.user}')
    client.loop.create_task(send_buys())

@app.route('/')
def home():
    return "✅ Bot do Discord está rodando!"

def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client.run(TOKEN)

# Inicia o bot em uma thread separada
threading.Thread(target=run_bot, daemon=True).start()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
