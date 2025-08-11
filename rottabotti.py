import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import asyncio
import time


#botin asetukset

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ytdlp asetukset ja definaus

ytdl_format_options = {
    "format": "bestaudio/best",
    "quiet": True,
    "extract_flat": False,
    "overwrite": True       # poistetaan mystinen error jossa ffmpeg luulee filtteriargumentteja tiedostoargumenteiksi...
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

ffmpeg_base_before = "-vn -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"

# kaikki temp storaget kirjastoina

queues = {} # {guild_id: [(url, title), (url, title)]}
filters = {} # {guild_id: [["filter1"], ["filter2"]]}
current_track = {} # {guild_id: (url, title)}
start_times = {} # {guild_id: unix timestamp}


MAX_QUERY_LENGTH = 100

# input sanitization
def sanitize(instring: str = ""):

    if len(instring) > MAX_QUERY_LENGTH:
        await interaction.response.send_message("liian pitkä query", ephemeral=True)
        return ""
    
    try:
        instring = ''.join(c for c in instring if c.isprintable())
        instring = discord.utils.escape_markdown(instring)
    except:
        await interaction.response.send_message("query sanitization failed", ephemeral=True)
        return ""

    return instring

    

# ffmpeg filter ja muitten asennusten rakennus:
def build_ffmpeg_options(guild_id: int) -> str:
    ffmpeg_opts = "-vn -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    if filters.get(guild_id):
        chain = ",".join(filters[guild_id]) # rakennetaan chain jossa kaikki filtterit tekstinä peräkkäin
        ffmpeg_opts += f" -af '{chain}'"

    return ffmpeg_opts


# musiikin soitto apufunktiot


# etitään ytdlp:llä youtube trackki
def ytdlp_find(ctx, query: str = "gangnam style"):
    # sanitisoidaan query ja jos sani failaa, siihen on jo vastattu
    safequery = sanitize(query)
    if safequery == "": return None, None

    # biisin tiedot, valitaan ensimmäinen joka löytyy
    info = ytdl.extract_info(f"ytsearch: {query}", download=False)["entries"][0]
    url = info["url"]
    title = info["title"]
    return url, title


# soitetaan nykyne track
def play_track(ctx, url: str, title: str, seek_seconds: int = 0):
    guild_id = ctx.guild.id
    vc = ctx.guild.voice_client
    # jos tarvii seekkaa nii tehrää niin
    if seek_seconds > 0:
        seek_opt = f"{ffmpeg_base_before} -ss {seek_seconds}"
    else:
        seek_opt = f"{ffmpeg_base_before}"

    ffmpeg_opts = build_ffmpeg_options(guild_id)

    vc.stop()
    if vc.is_playing():
        for i in range(10):
            if not vc.is_playing():
                break
            await asyncio.sleep(0.1)

    vc.play(discord.FFmpegPCMAudio(
        url,
        before_options=seek_opt,
        options=ffmpeg_opts
        ),
        after=lambda e: play_next(ctx)
        )

    current_track[guild_id] = (url, title)
    start_times[guild_id] = time.time() - seek_seconds


def play_next(ctx):
    guild_id = ctx.guild_id
    # jos queuessa tavaraa
    if queues.get(guild_id):
        url, title = queues[guild_id].pop(0)
        play_track(ctx, ult, title)
    # jos queue tyhjä
    else:
        current_track.pop(guild_id, none)
        start_times.pop(guild_id, None)



# botin automaattinen kanavalta poistuminen
def check_voice_channel_empty(ctx, vc):
    guild_id = ctx.guild.id
    wait_time = 0
    while vc.is_connected():
        channel = vc.channel

        non_bot = [m for m in channel.members if not m.bot]

        if len(non_bot) == 0:
            wait_time += 1
            if wait_time >= 5:
                filters[guild_id] = []
                queues[guild_id] = []
                current_track[guild_id] = []
                start_times[guild_id] = []
                await vc.disconnect()
                return

        else:
            wait_time = 0
        await asyncio.sleep(1)



# ----------------------------- #
# botti komennot ja sensellaset #
# ----------------------------- #

# käynnistys protokolla
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Error syncing commands: {e}")


# /nimi komento:
@bot.tree.command(name="nimi", description="vaiha jonku nimi")
@app_commands.describe(target="kenen nimi vaihetaan", new_name="mikä nimi annetaan")
async def name_command(interaction: discord.Interaction, target: discord.Member, new_name: str):
    if not interaction.user.guild_permissions.manage_nicknames:
        await interaction.response.send_message("sulta puuttuu permissionit vaihella nimiä", ephemeral=True)
        return

    try:
        await target.edit(nick=new_name)
        await interaction.response.send_message(f"vaihettiin {target.mention}:n nimeksi **{new_name}**", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(f"tällä tyypillä ({target.mention}) on bottia kovemmat permissionit, sen nimeä ei voi vaihtaa", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"virhe: exception {e}", ephemeral=False)


# /liity komento:
@bot.tree.command(name="liity", description="liity kanavalle")
async def join(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice_channel:
        await interaction.response.send_message("sun pitää olla voicessa että tää toimii", ephemeral=True)
        return

    channel = interaction.user.voice.channel
    if not interaction.guild.voice_client:
        try:
            await channel.connect()
            await interaction.response.send_message("liitytty kanavalle", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message("liittyminen epäonnistui, exception: {e}", ephemeral=False)


# /soita komento
@bot.tree.command(name="soita", description="soita musiikkia youtubesta")
@app_commands.describe(query="biisin nimi tai youtube url")
async def play(interaction: discord.Interaction, query: str):
    if not interaction.user.voice or not interaction.user.voice_channel:
        await interaction.response.send_message("et oo voicessa", ephemeral=True)
        return

    channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client
    guild_id = interaction.guild.id

    if not interaction.guild.voice_client:
        try:
            await voice_channel.connect()
        except Exception as e:
            await interaction.response.send_message("liittyminen epäonnistui, exception: {e}", ephemeral=False)
            return

    await interaction.response.send_message(f"etitään youtubesta '{query}", ephemeral=True)
    url, title = ytdlp_find(query)
    if url == None or title == None: return

    if guild_id not in queues:
        queues[guild_id] = []

    if not vc.is_playing():
        bot.loop.create_task(check_voice_channel_empty(interaction, vc))
        play_track(interaction, url, title)
        await interaction.followup.send(f"nyt soi: **{title}**", ephemeral=False)
    else:
        queues[guild_id].append((url, title))
        await interaction.followup.send(f"lisättiin jonoon: **{title}**")


# /jono komento
@bot.tree.command(name="jono", description="näytä jono")
async def show_queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id not in queues or not queues[guild_id]:
        await interaction.response.send_message("jono näyttää tyhjältä", ephemeral=True)
        return
    queue_list = "\n".join({f"{i+1}. {title}" for i, (_, title) in enumerate(queues[guild_id])})
    await interaction.response.send_message(f"**tällä hetkellä jonossa:**\n{queue_list}", ephemeral=False)


# /skipp komento
@bot.tree.command(name="skipp", description="skippaa soiva biisi")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction-response.send_message("skipattiin seuraavaan kappaleeseen", ephemeral=False)
    else:
        await interaction.response.send_message("ei skipattavaa", ephemeral=True)


# /lopeta komento
@bot.tree.command(name="lopeta", description="heihei botti")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    guild_id = interaction.guild.id
    if vc:
        filters[guild_id] = []
        queues[guild_id] = []
        current_track[guild_id] = []
        start_times[guild_id] = []
        vc.stop()
        await vc.disconnect()
        await interaction.response.send_message("poistuttu kanavalta", ephemeral=False)
    else:
        await interaction.response.send_message("eioo mitään mistä poistua", ephemeral=True)


# --------- #
# filtterit #
# --------- #


# runko custom filtterille
async def customrunko(interaction: discord.Interaction, value: int = 0):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client

    if not vc():
        await interaction.response.send_message("en oo kanavalla", ephemeral=True)
        return

    if guild_id not in filters:
        filters[guild_id] = []

    filters[guild_id] = [f for f in filters[guild_id] if not f.startswith ("filtteriteksti")]
    filters[guild_id].append(f"filtteriteksti")


    url, title = current_track[guild_id]
    elapsed = int(time.time() - start_times.get(guild_id, 0))
    await play_track(ctx, url, title, elapsed)


# runko toggle filtterille
async def togglerunko(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client

    if not vc:
        await interaction.response.send_message("en oo kanavalla", ephemeral=True)
        return

    if guild_id not in filters:
        filters[guild_id] = []

    # filtteri määritys
    filtteri = "filtteri"


    # toggle päälle pois
    if filtteri in filters[guild_id]:
        filters[guild_id].remove(filtteri)
        await interaction.response.send_message("filtteri pois", ephemeral=False)
    else:
        filters[guild_id].append(filtteri)
        await interaction.response.send_message("filtteri päällä", ephemeral=False)

    url, title = current_track[guild_id]
    elapsed = int(time.time() - start_times.get(guild_id, 0))
    await play_track(ctx, url, title, elapsed)



# custom bass boost filter
@bot.tree.command(name="filtercustombass", description="custom bass boost filtteri")
@app_commands.describe(gain="paljonko muutetaan, max 50, min -50")    
async def bass(interaction: discord.Interaction, gain: int = 10):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client

    if not vc():
        await interaction.response.send_message("en oo kanavalla", ephemeral=True)
        return

    if guild_id not in filters:
        filters[guild_id] = []

    gain = max(-50, min(gain, 50)) # max 50 min -50

    filters[guild_id] = [f for f in filters[guild_id] if not f.startswith ("equalizer=")]
    filters[guild_id].append(f"equalizer=f=40:width_type=h:width=50:g={gain}")

    if gain > 0:
        if gain == 50:
            await interaction.response.send_message(f"amis bassot aktivoitu", ephemeral=False)
        else:
            await interaction.response.send_message(f"bassoa nostettu {gain}dB", ephemeral=False)
    elif gain < 0:
        await interaction.response.send_message(f"bassoa madallettu {gain}dB", ephemeral=False)
    else:
        await interaction.response.send_message(f"bassboost disabloitu")


    url, title = current_track[guild_id]
    elapsed = int(time.time() - start_times.get(guild_id, 0))
    await play_track(ctx, url, title, elapsed)


# toggle bass boost filtteri
@bot.tree.command(name="filterbass", description="bass boost filtteri")
async def togglerunko(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client

    if not vc:
        await interaction.response.send_message("en oo kanavalla", ephemeral=True)
        return

    if guild_id not in filters:
        filters[guild_id] = []

    # filtteri määritys
    filtteri = "equalizer=f=40:width_type=h:width=50:g=10"


    # toggle päälle pois
    if filtteri in filters[guild_id]:
        filters[guild_id].remove(filtteri)
        await interaction.response.send_message("bass boost pois päältä", ephemeral=False)
    else:
        filters[guild_id].append(filtteri)
        await interaction.response.send_message("bass boost päällä", ephemeral=False)

    url, title = current_track[guild_id]
    elapsed = int(time.time() - start_times.get(guild_id, 0))
    await play_track(ctx, url, title, elapsed)


# amis bass boost toggle
@bot.tree.command(name="filteramis", description="amis bass boost")
async def togglerunko(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client

    if not vc:
        await interaction.response.send_message("en oo kanavalla", ephemeral=True)
        return

    if guild_id not in filters:
        filters[guild_id] = []

    # filtteri määritys
    filtteri = "equalizer=f=40:width_type=h:width=50:g=50"


    # toggle päälle pois
    if filtteri in filters[guild_id]:
        filters[guild_id].remove(filtteri)
        await interaction.response.send_message("amis moodi pois päältä", ephemeral=False)
    else:
        filters[guild_id].append(filtteri)
        await interaction.response.send_message("amis moodi päällä", ephemeral=False)

    url, title = current_track[guild_id]
    elapsed = int(time.time() - start_times.get(guild_id, 0))
    await play_track(ctx, url, title, elapsed)


# nightcore filter
@bot.tree.command(name="filteranime", description="nightcore filter")
async def togglerunko(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client

    if not vc:
        await interaction.response.send_message("en oo kanavalla", ephemeral=True)
        return

    if guild_id not in filters:
        filters[guild_id] = []

    # filtteri määritys
    filtteri = "asetrate=44100*1.25,aresample=44100,atempo=1.25"


    # toggle päälle pois
    if filtteri in filters[guild_id]:
        filters[guild_id].remove(filtteri)
        await interaction.response.send_message("nightcore moodi pois päältä", ephemeral=False)
    else:
        filters[guild_id].append(filtteri)
        await interaction.response.send_message("nightcore moodi päällä", ephemeral=False)

    url, title = current_track[guild_id]
    elapsed = int(time.time() - start_times.get(guild_id, 0))
    await play_track(ctx, url, title, elapsed)


@bot.tree.command(name="filtersigma", description="epic sigma gigachad filtteri")
async def togglerunko(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client

    if not vc:
        await interaction.response.send_message("en oo kanavalla", ephemeral=True)
        return

    if guild_id not in filters:
        filters[guild_id] = []

    # filtteri määritys
    filtteri = "asetrate=44100*0.85,aresample=44100"


    # toggle päälle pois
    if filtteri in filters[guild_id]:
        filters[guild_id].remove(filtteri)
        await interaction.response.send_message("sigma moodi pois päältä", ephemeral=False)
    else:
        filters[guild_id].append(filtteri)
        await interaction.response.send_message("sigma moodi päällä", ephemeral=False)

    url, title = current_track[guild_id]
    elapsed = int(time.time() - start_times.get(guild_id, 0))
    await play_track(ctx, url, title, elapsed)






# botti päälle
with open("/opt/rottabotti/.env", "r") as tokenfile:
    TOKEN=tokenfile.read().strip()

bot.run(TOKEN)