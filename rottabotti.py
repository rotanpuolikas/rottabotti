import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import asyncio
import time
import json
import os


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

queues = {} # {guild_id: [(url, title, duration), (url, title, duration)]}
filters = {} # {guild_id: [["filter1"], ["filter2"]]}
current_track = {} # {guild_id: (url, title, duration)}
start_times = {} # {guild_id: unix timestamp}
looping = {} # {guild_id: [(url, title, duration), (url, title, duration)]}
suppress_after = {} # {guild_id: bool}
channels = load_channels() # {guild_id: channel_id}, ladataan apufunktion kautta


MAX_QUERY_LENGTH = 100

# input sanitization
def sanitize(instring: str):

    if len(instring) > MAX_QUERY_LENGTH:
        return ""
    
    try:
        instring = ''.join(c for c in instring if c.isprintable())
        instring = discord.utils.escape_markdown(instring)
    except:
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
    
    # custom teksti promptit pelottaa
    safequery = sanitize(query)
    if safequery == "":
        info = ytdl.extract_info(f"ytsearch: pelle hermanni theme", download=False)["entries"][0]

    # biisin tiedot, valitaan ensimmäinen joka löytyy
    else:
        info = ytdl.extract_info(f"ytsearch: {query}", download=False)["entries"][0]
    url = info["url"]
    title = info["title"]
    duration = info["duration"]
    return url, title, duration


# soitetaan nykyne track
async def play_track(ctx, url: str, title: str, duration: int, seek_seconds: int = 0, autoplay: bool = True):
    guild_id = ctx.guild.id
    vc = ctx.guild.voice_client

    suppress_after[guild_id] = not autoplay


    # jos tarvii seekkaa nii tehrää niin
    if seek_seconds > 0:
        seek_opt = f"{ffmpeg_base_before} -ss {seek_seconds}"
        bot.loop.create_task(tracksong(ctx, vc))
    else:
        seek_opt = f"{ffmpeg_base_before}"

    ffmpeg_opts = build_ffmpeg_options(guild_id)

    vc.stop()
    if vc.is_playing():
        for i in range(10):
            if not vc.is_playing():
                break
            await asyncio.sleep(0.1)

    asyncloop = asyncio.get_running_loop()

    def after_wrapper(error):
        if error:
            print(f"Error in after_wrapper: {error}")
        #only trigger if not suppressed
        
        if not suppress_after.get(guild_id, False):
            asyncio.run_coroutine_threadsafe(play_next(ctx), asyncloop)

    vc.play(discord.FFmpegPCMAudio(
        url,
        before_options=seek_opt,
        options=ffmpeg_opts
        ),
        after=after_wrapper
        )


    current_track[guild_id] = (url, title, duration)
    start_times[guild_id] = time.time() - seek_seconds


async def play_next(ctx, seek_seconds: int = 0):
    guild_id = ctx.guild.id
    # jos queuessa tavaraa
    if queues.get(guild_id):
        url, title, duration = queues[guild_id].pop(0)
        await play_track(ctx, url, title, duration)
    # jos queue tyhjä
    else:
        current_track.pop(guild_id, None)
        start_times.pop(guild_id, None)



# botin automaattinen kanavalta poistuminen
async def check_voice_channel_empty(ctx, vc):
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
                current_track[guild_id] = ()
                start_times[guild_id] = []
                await vc.disconnect()
                return

        else:
            wait_time = 0
        await asyncio.sleep(1)


async def checkqueue_vc(ctx, vc):
    guild_id = ctx.guild.id
    wait_time = 0
    while vc.is_connected():
        print(f"current track: {current_track[guild_id][1]}, wait_time: {wait_time}")
        if vc.is_playing():
            wait_time = 0
        else:
            if wait_time >= 10:
                filters[guild_id] = []
                queues[guild_id] = []
                current_track[guild_id] = ()
                start_times[guild_id] = []
                await vc.disconnect()
                return

            if queues[guild_id] != []:
                await play_next(ctx)
            
            else:
                wait_time += 1

        await asyncio.sleep(1)

# trackataan tänhetkisen biisin progressia
async def tracksong(ctx, vc):
    guild_id = ctx.guild_id
    while vc.is_connected():
        if current_track[guild_id] != []:
            url, title, duration = current_track[guild_id]
            elapsed = int(time.time() - start_times.get(guild_id, 0))
            if duration - elapsed < -5: #jos biisin ois pitäny jo loppua
                await play_next(ctx)
                break
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
    if not interaction.user.voice or not interaction.user.voice.channel:
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
    try:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("et oo voicessa", ephemeral=True)
            return

        channel = interaction.user.voice.channel

        if not interaction.guild.voice_client:
            try:
                await channel.connect()
            except Exception as e:
                await interaction.response.send_message("liittyminen epäonnistui, exception: {e}", ephemeral=False)
                return

        await interaction.response.send_message(f"etitään youtubesta **{query}**", ephemeral=True)
        url, title, duration = ytdlp_find(interaction, query)
        if url == None or title == None:
            await interaction.followup.send(f"query failed sanitization", ephemeral=True)

        guild_id = interaction.guild.id
        if guild_id not in queues:
            queues[guild_id] = []

        vc = interaction.guild.voice_client
        if not vc.is_playing():
            bot.loop.create_task(check_voice_channel_empty(interaction, vc))
            #bot.loop.create_task(checkqueue_vc(interaction, vc))
            await play_track(interaction, url, title, duration)
            await interaction.followup.send(f"nyt soi: **{title}**", ephemeral=False)
        else:
            queues[guild_id].append((url, title, duration))
            await interaction.followup.send(f"lisättiin jonoon: **{title}**")
        seconds = duration
        hours = duration // 3600
        minutes = (duration % 3600) // 60
        seconds = duration % 60
        lenstring = "Biisin kesto: **"
        if hours > 0:
            lenstring += f"{hours} h "
        if minutes > 0:
            lenstring += f"{minutes} min "
        if seconds > 0:
            lenstring += f"{seconds} sec"
        lenstring += "**"
        await interaction.followup.send(f"{lenstring}")
    except:
        return


# /jono komento
@bot.tree.command(name="jono", description="näytä jono")
async def show_queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id not in queues or not queues[guild_id]:
        await interaction.response.send_message("jono näyttää tyhjältä", ephemeral=True)
        return
    queue_list = "\n".join({f"{i+1}. {title}" for i, (_, title) in enumerate(queues[guild_id])})
    await interaction.response.send_message(f"**Nyt soi:**\n{current_track[guild_id][1]}\n\n**tällä hetkellä jonossa:**\n{queue_list}", ephemeral=False)


# /skipp komento
@bot.tree.command(name="skipp", description="skippaa soiva biisi")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await play_next(interaction)
        await interaction.response.send_message("skipattiin seuraavaan kappaleeseen", ephemeral=False)
    else:
        await interaction.response.send_message("ei skipattavaa", ephemeral=True)

"""
# /loop komento
@bot.tree.command(name="loop", description="loop moodi toggler")
@app_commands.describe(mode="pois(x), biisi(b) tai jono(q)")
async def loop(interaction: discord.Interaction, mode=str):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client
    if not vc:
        await interaction.response.send_message("en oo kanavalla")
        return

    if mode[0].lower() not in "pxbjq":
        await interaction.response.send_message("incompat moodi")
        return

    moodi = mode[0].lower()

    if moodi in "px":
        looping[guild_id]
"""


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

    if vc.is_playing():
        url, title, duration = current_track[guild_id]
        elapsed = int(time.time() - start_times.get(guild_id, 0))
        await play_track(interaction, url, title, duration, elapsed, False)


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

    if vc.is_playing():
        url, title, duration = current_track[guild_id]
        elapsed = int(time.time() - start_times.get(guild_id, 0))
        # soitetaan ja annetaan parametreinä interactio, biisin tiedot, kauanko menny ja tärkeä AUTOPLAY: False
        await play_track(interaction, url, title, duration, elapsed, False)



# custom bass boost filter
@bot.tree.command(name="filtercustombass", description="custom bass boost filtteri")
@app_commands.describe(gain="paljonko muutetaan, max 50, min -50")    
async def custombass(interaction: discord.Interaction, gain: int = 10):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client

    if not vc:
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

    if vc.is_playing():
        url, title, duration = current_track[guild_id]
        elapsed = int(time.time() - start_times.get(guild_id, 0))
        await play_track(interaction, url, title, duration, elapsed, False)


# toggle bass boost filtteri
@bot.tree.command(name="filterbass", description="bass boost filtteri")
async def bassfilter(interaction: discord.Interaction):
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

    if vc.is_playing():
        url, title, duration = current_track[guild_id]
        elapsed = int(time.time() - start_times.get(guild_id, 0))
        await play_track(interaction, url, title, duration, elapsed, False)


# amis bass boost toggle
@bot.tree.command(name="filteramis", description="amis bass boost")
async def amisfilter(interaction: discord.Interaction):
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

    if vc.is_playing():
        url, title, duration = current_track[guild_id]
        elapsed = int(time.time() - start_times.get(guild_id, 0))
        await play_track(interaction, url, title, duration, elapsed, autoplay=False)


# nightcore filter
@bot.tree.command(name="filteranime", description="nightcore filter")
async def animefilter(interaction: discord.Interaction):
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

    if vc.is_playing():
        url, title, duration = current_track[guild_id]
        elapsed = int(time.time() - start_times.get(guild_id, 0))
        await play_track(interaction, url, title, duration, elapsed, False)

# sigma gigachad filtteri
@bot.tree.command(name="filtersigma", description="epic sigma gigachad filtteri")
async def sigmafilter(interaction: discord.Interaction):
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

    if vc.is_playing():
        url, title, duration = current_track[guild_id]
        elapsed = int(time.time() - start_times.get(guild_id, 0))
        await play_track(interaction, url, title, duration, elapsed, False)


# kaikki filtterit pois
@bot.tree.command(name="filterpois", description="kaikki filtterit kerralla pois")
async def filterpois(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client

    if not vc:
        await interaction.response.send_message("en oo kanavalla", ephemeral=True)
        return

    filters[guild_id] = []
    await interaction.response.send_message("filtterit resetoitu", ephemeral=False)


    if vc.is_playing():
        url, title, duration = current_track[guild_id]
        elapsed = int(time.time() - start_times.get(guild_id, 0))
        await play_track(interaction, url, title, duration, elapsed, False)




# --------------- #
# muita komentoja #
# --------------- #



# config utility funktiot
def load_channels():
    if os.path.exists(CHANNEL_FILE):
        with open(CHANNEL_FILE, "r") as f:
            return json.load(f)
    return {} # tyhjä kirjasto jos ei oo olemassa

def save_channels(data):
    with open(CHANNEL_FILE, "w") as f:
        json.dump(data, f, indent=4)

async def sendtochannel(ctx, message: str):
    guild_id = ctx.guild_id
    if guild_id not in channels:
        await ctx.send("default kanavaa eioo määritetty")
        return
    
    channel_id = channels[guild_id]
    channel = bot.get_channel(channel_id)

    if channel:
        await channel.send(message)
    else:
        await ctx.send("epäonnistuttu viestin lähettämisessä")

@bot.tree.command(name="configchannel", description="konfiguroi kanava johon viestit laitetaan")
@app_commands.describe(channel="valitte kanava")
@commands.has_permissions(administrator=True)
async def setchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = interaction.guild_id
    channels[guild_id] = channel.id
    save_channels(channels)
    await interaction.response.send_message(f"Kanavaksi vaihdettu {channel.mention}")




CHANNEL_FILE = "./channelconfig.json"

# ladataan kanavat joihin viesti laitetaan
with open("./channelconfig.json", "r") as f:
    config = json.load(f)


# botti päälle
with open("/opt/rottabotti/.env", "r") as tokenfile:
    TOKEN=tokenfile.read().strip()

bot.run(TOKEN)