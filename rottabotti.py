import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
import time

MAX_QUERY_LENGTH = 100

# Enable members intent
intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

ytdl_format_options = {
    "format": "bestaudio/best",
    "quiet": True,
    "extract_flat": False,
}
ffmpeg_options = {
    "options": "-vn -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"  # audio only
}

ffmpeg_base_before = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
ytdl = youtube_dl.YoutubeDL(ytdl_format_options)


#queue storage!!1!!!
queues = {}

filters = {}

current_track = {}  # {guild_id: (url, title)}

start_times = {}

async def restart_with_filter(ctx, elapsed):
    guild_id = ctx.guild.id
    vc = ctx.guild.voice_client

    if not vc or not vc.is_connected():
        return

    url, title = current_track[guild_id]
    filter_str = filters.get(guild_id, "")
    seek_opt = f"{ffmpeg_base_before} -ss {elapsed}" if elapsed > 0 else ffmpeg_base_before

    # Stop current playback first
    vc.stop()
    await asyncio.sleep(0.3)  # let Discord close the old stream

    vc.play(
        discord.FFmpegPCMAudio(
            url,
            before_options=seek_opt,
            options=f"-vn {filter_str}"
        ),
        after=lambda e: play_next(ctx)
    )

    current_track[guild_id] = (url, title)
    start_times[guild_id] = time.time() - elapsed

def play_track(ctx, url, title, seek_seconds=0):
    guild_id = ctx.guild.id
    filter_str = filters.get(guild_id, "")
    seek_opt = f"{ffmpeg_base_before} -ss {seek_seconds}" if seek_seconds > 0 else ffmpeg_base_before

    ctx.guild.voice_client.play(
        discord.FFmpegPCMAudio(
            url,
            before_options=seek_opt,
            options=f"-vn {filter_str}"
        ),
        after=lambda e: play_next(ctx)
    )
    current_track[guild_id] = (url, title)
    start_times[guild_id] = time.time() - seek_seconds  # Adjust start time

def play_next(ctx):
    guild_id = ctx.guild.id
    if queues.get(guild_id):
        url, title = queues[guild_id].pop(0)
        play_track(ctx, url, title)
    else:
        # Nothing queued
        current_track.pop(guild_id, None)
        start_times.pop(guild_id, None)



async def check_voice_channel_empty(vc):
    # vc = VoiceClient instance
    wait_time = 0
    while vc.is_connected():
        channel = vc.channel
        # Count how many non-bot members are in the channel
        non_bot_members = [m for m in channel.members if not m.bot]

        if len(non_bot_members) == 0:
            if wait_time == 0:
                print("ei muita puhelussa, lähetään in 5 seconds")
            wait_time += 1
            if wait_time >= 5:
                await vc.disconnect()
                print("lähettiin koska inaktiivinen")
                return
        else:
            wait_time = 0
        await asyncio.sleep(1)

with open("/opt/rottabotti/.env", "r") as f:
    TOKEN=f.read().strip()

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Error syncing commands: {e}")

@bot.tree.command(name="nimi", description="vaiha jonku nimi")
@app_commands.describe(
    target="kenen nimi vaihetaan",
    new_name="mikä nimi annetaan"
)
async def name_command(interaction: discord.Interaction, target: discord.Member, new_name: str):
    # Check if the bot has permission
    if not interaction.user.guild_permissions.manage_nicknames:
        await interaction.response.send_message("❌ sää et voi jonku takia vaihella nimiä", ephemeral=True)
        return

    try:
        await target.edit(nick=new_name)
        await interaction.response.send_message(f"✅ vaihettiin {target.mention}:n nimeksi **{new_name}**, nauttikoot uudesta nimestään", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("❌ HÄHÄÄÄ et voi vaihtaa mun nimmee t rotta >:))", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"⚠️ apua soz mega virhe kaikki paskaksi: {e}", ephemeral=False)


#join komento

@bot.tree.command(name="liity", description="pakota botti liittyyn just sun kanavalle, lähinnä debuggausta varten")
@app_commands.describe()
async def join(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("❌ mee eka kanavalle", ephemeral=True)
        return

    voice_channel = interaction.user.voice.channel
    if not interaction.guild.voice_client:
        try:
            await voice_channel.connect()
            interaction.response.send_message("liitytty", ephemeral=True)
        except:
            await interaction.response.send_message("joku failas joinaamisessa, maybe permission issue, maybe bot crash, en tiiä", ephemeral=False)


# ==== /play COMMAND ====
@bot.tree.command(name="soita", description="eti biisi youtubeta ja soita (tai lisää queueen)")
@app_commands.describe(query="biisin nimi tai youtupe urli")
async def play(interaction: discord.Interaction, query: str):
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("❌ mee eka kanavalle", ephemeral=True)
        return

    voice_channel = interaction.user.voice.channel
    if not interaction.guild.voice_client:
        try:
            await voice_channel.connect()
        except:
            return

    await interaction.response.send_message(f"🔍 etitään youtupesta: `{query}`", ephemeral=True)

    try:
        if len(query) > MAX_QUERY_LENGTH:
            await interaction.followup.send("hei liian pitkä query, either koitit tehä jotain ilkeetä tai sitte sulla on tosi pitkä linkki, either way ei onnaa nyt tämmönen hei")
            return
        try:
            query = ''.join(c for c in query if c.isprintable())
            query = discord.utils.escape_markdown(query)
        except:
            await interaction.followup.send("sun query oli jotenki ilikee, en suostu prosessoimaan >:(")
            return
        info = ytdl.extract_info(f"ytsearch:{query}", download=False)["entries"][0]
        url = info["url"]
        title = info["title"]
    except Exception as e:
        await interaction.followup.send(f"⚠️ virhe, joko en löytäny tai sitte exception: {e}", ephemeral=True)
        await interaction.followup.send(f"jos virhe on 'list index out of range', todnäk yritit laittaa playlistiä mikä eioo mahollista")
        return

    guild_id = interaction.guild.id
    if guild_id not in queues:
        queues[guild_id] = []

    vc = interaction.guild.voice_client
    if not vc.is_playing():
        bot.loop.create_task(check_voice_channel_empty(vc))
        play_track(interaction, url, title)
        await interaction.followup.send(f"🎵 ny soi: **{title}**", ephemeral=False)
    else:
        queues[guild_id].append((url, title))
        await interaction.followup.send(f"lisättiin queueen: **{title}**", ephemeral=False)

# ==== /queue COMMAND ====
@bot.tree.command(name="jono", description="mitä jonossa")
async def show_queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id not in queues or not queues[guild_id]:
        await interaction.response.send_message("eioo mittään quessa", ephemeral=True)
    else:
        queue_list = "\n".join([f"{i+1}. {title}" for i, (_, title) in enumerate(queues[guild_id])])
        await interaction.response.send_message(f"📜 **tällä hetkellä quessa:**\n{queue_list}", ephemeral=False)

# ==== /skip COMMAND ====
@bot.tree.command(name="skipp", description="jjjja seurraavaa kiitos")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message("⏭️ skipattiin tuo äsköne, iha surkee video muutenki", ephemeral=False)
    else:
        await interaction.response.send_message("❌ mittään ei soi atm", ephemeral=True)

# ==== /stop COMMAND ====
@bot.tree.command(name="lopeta", description="lopeta musisointi heti")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        queues[interaction.guild.id] = []
        vc.stop()
        await vc.disconnect()
        await interaction.response.send_message("⏹️ oke meen pois sitte kerta", ephemeral=False)
    else:
        await interaction.response.send_message("❌ hei emmää ees soita mittään", ephemeral=True)


#filtterit

@bot.tree.command(name="bass", description="muumit massive bass")
@app_commands.describe(gain="monellako desibelillä buustataan, max 50, min -50")
async def bass(interaction: discord.Interaction, gain: int = 10):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client

    if not vc or not vc.is_playing():
        await interaction.response.send_message("❌ mittään ei soi", ephemeral=True)
        return

    if gain > 50: gain = 50
    if gain < -50: gain = -50
    filters[guild_id] = f"-af equalizer=f=40:width_type=h:width=50:g={gain}"

    if gain > 0:
        if gain == 50:
            await interaction.response.send_message(f"AMISMUUMIT AKTIVOITU!!1!!!")
        else:
            await interaction.response.send_message(f"muumit massive bass aktivoitu ({gain}dB)", ephemeral=False)

    elif gain < 0:
        await interaction.response.send_message(f"muumit trivial bass aktivoitu ({gain}dB)")

    elif gain == 0:
        await interaction.response.send_message(f"normi muumit aktivoitu (0dB)")

    else:
        await interaction.response.send_message(f"mysteeri muumit aktivoitu, joku logiikka meni pieleen ({gain}dB)")

    elapsed = int(time.time() - start_times.get(guild_id, 0))
    await restart_with_filter(interaction, elapsed)




@bot.tree.command(name="filterpois", description="ottaa kaikki audiofiltterit pois")
async def clearfilter(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client

    if not vc or not vc.is_playing():
        await interaction.response.send_message("❌ mittään ei soi", ephemeral=True)
        return

    filters[guild_id] = ""

    await interaction.response.send_message("mayhem ohi, filtterit poistettu", ephemeral=False)

    elapsed = int(time.time() - start_times.get(guild_id, 0))
    await restart_with_filter(interaction, elapsed)



bot.run(TOKEN)
