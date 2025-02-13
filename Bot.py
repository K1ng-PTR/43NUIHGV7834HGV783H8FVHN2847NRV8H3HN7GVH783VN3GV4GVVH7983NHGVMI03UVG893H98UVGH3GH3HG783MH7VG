import discord
import aiohttp
import asyncio
import threading
from flask import FlaskAPI_URL

# Configurações do bot
TOKEN = "DISCORD_BOT_TOKEN"
CHANNEL_ID = 123456789012345678  # Certifique-se de usar um número, não string
API_URL = "https://api-cjng.onrender.com/buys"

# Configurações do Flask
app = Flask(__name__)

intents = discord.Intents.default()
client = discord.Client(intents=intents)

async def fetch_buys():
    async with aiohttp.ClientSession() as session:
        async with session.get(API_URL) as response:
            if response.status == 200:
                return await response.json()
            return []

async def send_buys():
    await client.wait_until_ready()
    channel = client.get_channel(CHANNEL_ID)
    if not channel:
        print("Canal não encontrado!")
        return
    
    while not client.is_closed():
        buys = await fetch_buys()
        for buy in buys:
            embed = discord.Embed(title="Nova Compra Confirmada!", color=discord.Color.green())
            embed.add_field(name="Comprador", value=buy["comprador"], inline=False)
            embed.add_field(name="Tipo de Chave", value=buy["tipo_chave"], inline=False)
            embed.add_field(name="Chave Gerada", value=buy["chave"], inline=False)
            embed.add_field(name="Checkout URL", value=buy["checkout_url"], inline=False)
            await channel.send(embed=embed)
        await asyncio.sleep(30)  # Verifica a API a cada 30 segundos

@client.event
async def on_ready():
    print(f'Bot conectado como {client.user}')
    client.loop.create_task(send_buys())

# Rota Flask para verificar se o servidor está rodando
@app.route('/')
def home():
    return "Bot do Discord está rodando!"

# Função para rodar o bot em uma thread separada
def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client.run(TOKEN)

# Iniciar o bot em uma nova thread
threading.Thread(target=run_bot, daemon=True).start()

# Iniciar o servidor Flask
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
