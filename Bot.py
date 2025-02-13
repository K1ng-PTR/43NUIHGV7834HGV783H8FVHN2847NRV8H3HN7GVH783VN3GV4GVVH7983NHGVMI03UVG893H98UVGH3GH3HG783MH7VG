import discord
import aiohttp
import asyncio

TOKEN = "SEU_DISCORD_BOT_TOKEN"
CHANNEL_ID = "SEU_DISCORD_CHANNEL_ID"
API_URL = "https://api-cjng.onrender.com/buys"

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

client.run(TOKEN)
