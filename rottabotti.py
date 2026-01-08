import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import asyncio
import time
import json
import os
import random

import re
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# botin asetukset


intents = discord.Intents.default()
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ytdlp asetukset ja definaus

ytdl_format_options = {
    "format": "bestaudio/best",
    "quiet": True,
    "extract_flat": False,
    "overwrite": True,  # poistetaan mystinen error jossa ffmpeg luulee filtteriargumentteja tiedostoargumenteiksi...
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

ffmpeg_base_before = "-vn -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"

# kaikki temp storaget kirjastoina

queues = {}  # {guild_id: [(url, title, duration), (url, title, duration)]}
filters = {}  # {guild_id: [["filter1"], ["filter2"]]}
current_track = {}  # {guild_id: (url, title, duration)}
start_times = {}  # {guild_id: unix timestamp}
looping = {}  # {guild_id: [(url, title, duration), (url, title, duration)]}
suppress_after = {}  # {guild_id: bool}
channels = {}  # {guild_id: channel_id}, ladataan myöhemmin apufunktiossa
paused = {}  # {guild_id: stop_time} pidetään trackkia siitä onko biisi pausella
mayhem = {}  # {guild_id: mayhem?} bool
searching = {}  # {guild_id: searching?} bool


MAX_QUERY_LENGTH = 100

clidfile = "/opt/rottabotti/client"
clsecfile = "/opt/rottabotti/secret"

with open(clidfile, "r") as clidf:
    clid = clidf.read().strip()

with open(clsecfile, "r") as clsecf:
    clsec = clsecf.read().strip()

sp = spotipy.Spotify(
    auth_manager=SpotifyClientCredentials(
        client_id=clid,
        client_secret=clsec,
    )
)


SPOTIFY_TRACK_REGEX = re.compile(r"https?://open\.spotify\.com/track/([a-zA-Z0-9]+)")
SPOTIFY_PLAYLIST_REGEX = re.compile(
    r"https?://open\.spotify\.com/(playlist|album)/([a-zA-Z0-9]+)"
)


def is_spotify_track(url: str) -> bool:
    return bool(SPOTIFY_TRACK_REGEX.match(url))


def spotify_to_query(spotify_url: str) -> str | None:
    try:
        track = sp.track(spotify_url)
        artist = track["artists"][0]["name"]
        title = track["name"]
        return f"{artist} - {title}"
    except Exception as e:
        print(f"Spotify parse failed: {e}")
        return None


def spotify_playlist_to_queries(playlist_url: str) -> list[str]:
    """
    Returns a list of strings in "Artist - Title" format for all tracks in a Spotify playlist or album.
    """
    try:
        results = (
            sp.playlist_items(playlist_url)
            if "playlist" in playlist_url
            else sp.album_tracks(playlist_url)
        )
        tracks = []
        for item in results["items"]:
            # Spotify playlist returns dict with 'track'
            track_info = item["track"] if "track" in item else item
            artist = track_info["artists"][0]["name"]
            title = track_info["name"]
            tracks.append(f"{artist} - {title}")
            if len(tracks) >= 50:
                return tracks
        return tracks
    except Exception as e:
        print(f"Spotify playlist parse failed: {e}")
        return []


async def enqueue_spotify_tracks(ctx, tracks: list[str]):
    """
    Add Spotify tracks to the queue gradually.
    The first track is played immediately.
    """
    guild_id = ctx.guild.id
    if guild_id not in queues:
        queues[guild_id] = []

    first_track_done = False

    for track_query in tracks:
        url, title, duration, success = ytdlp_find(ctx, track_query)
        if not success:
            print(f"Failed to fetch {track_query}")
            continue

        if not first_track_done:
            # If nothing is playing, start first track immediately
            vc = ctx.guild.voice_client
            if not vc.is_playing():
                await play_track(ctx, url, title, duration)
                first_track_done = True
                await songinfo(ctx, title, duration)
                continue

        vc = ctx.guild.voice_client
        if not vc:
            break
        # Add the rest to the queue
        queues[guild_id].append((url, title, duration))
        # await songinfo(ctx, title, duration, now=False)
        await asyncio.sleep(1)  # wait 1s between adding tracks
    await ctx.followup.send("Playlistin biisit lisätty onnistuneesti", ephemeral=False)


# input sanitization
def sanitize(instring: str):

    if len(instring) > MAX_QUERY_LENGTH:
        return ""

    try:
        instring = "".join(c for c in instring if c.isprintable())
        instring = discord.utils.escape_markdown(instring)
    except:
        return ""

    return instring


# ffmpeg filter ja muitten asennusten rakennus:
def build_ffmpeg_options(guild_id: int) -> str:
    ffmpeg_opts = "-vn -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    if filters.get(guild_id):
        chain = ",".join(
            filters[guild_id]
        )  # rakennetaan chain jossa kaikki filtterit tekstinä peräkkäin
        ffmpeg_opts += f" -af '{chain}'"

    return ffmpeg_opts


# musiikin soitto apufunktiot


# etitään ytdlp:llä youtube trackki
def ytdlp_find(ctx, query: str = "gangnam style"):

    # custom teksti promptit pelottaa
    safequery = sanitize(query)
    if safequery == "":
        info = ytdl.extract_info(f"ytsearch: pelle hermanni theme", download=False)[
            "entries"
        ][0]

    # biisin tiedot, valitaan ensimmäinen joka löytyy
    else:
        try:
            info = ytdl.extract_info(f"ytsearch: {query}", download=False)["entries"][0]
        except:
            return None, None, None, False
    if not info["url"] or not info["title"] or not info["duration"]:
        return None, None, None, False

    url = info["url"]
    title = info["title"]
    duration = info["duration"]
    return url, title, duration, True


# soitetaan nykyne track
async def play_track(
    ctx,
    url: str,
    title: str,
    duration: int,
    seek_seconds: int = 0,
    autoplay: bool = True,
    skipped: bool = False,
):
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
        # only trigger if not suppressed

        if not suppress_after.get(guild_id, False):
            if not skipped:
                asyncio.run_coroutine_threadsafe(play_next(ctx), asyncloop)

    vc.play(
        discord.FFmpegPCMAudio(url, before_options=seek_opt, options=ffmpeg_opts),
        after=after_wrapper,
    )

    current_track[guild_id] = (url, title, duration)
    start_times[guild_id] = time.time() - seek_seconds


async def play_next(ctx, seek_seconds: int = 0, skipped: bool = False):
    guild_id = ctx.guild.id

    # jos looping päällä
    if looping.get(guild_id):
        # print(f"looping päällä, looplista: {looping[guild_id]}") #debug
        url, title, duration = looping[guild_id].pop(0)
        looping[guild_id].append((url, title, duration))
        await play_track(ctx, url, title, duration)
        return

    else:
        # jos queuessa tavaraa
        if queues.get(guild_id):
            url, title, duration = queues[guild_id].pop(0)
            await songinfo(ctx, title, duration)
            await play_track(ctx, url, title, duration, skipped)
            return
        # jos queue tyhjä
        else:
            current_track.pop(guild_id, None)
            start_times.pop(guild_id, None)
            return


# biisin nimi ja kesto tulostus
async def songinfo(ctx, title, duration, now: bool = True, next: bool = False):
    if now:
        whenplays = "Nyt soi: "
    elif next:
        whenplays = "Lisätty seuraavaksi: "

    else:
        whenplays = "Lisätty jonoon: "
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
    # print(f"tulostetaan: {whenplays}: **{title}**\n{lenstring}") #debug
    await sendtochannel(ctx, f"{whenplays}**{title}**\n{lenstring}")


# voiceen connectaus funktio
async def connectVoice(ctx, playsound: bool = False):
    if not ctx.user.voice or not ctx.user.voice.channel:
        await ctx.response.send_message(
            "sun pitää olla voicessa että tää toimii", ephemeral=True
        )
        return

    channel = ctx.user.voice.channel

    if not ctx.guild.voice_client:
        try:
            await channel.connect()
            if playsound:
                vc = ctx.guild.voice_client
                vc.play(discord.FFmpegPCMAudio("/home/rottabotti/audio/joinsound.mp3"))
            return True
        except Exception as e:
            await ctx.response.send_message(f"liittyminen epäonnistui, exception: {e}")
            return False
    else:
        return True


# --------------- #
# taustaprosessit #
# --------------- #


async def randomsound(ctx):
    MINIMUM = 30
    MAXIMUM = 300

    vc = ctx.guild.voice_client
    guild_id = ctx.guild.id

    audios = [
        "/home/rottabotti/audio/teemo1.ogx",
        "/home/rottabotti/audio/teemo2.ogx",
        "/home/rottabotti/audio/teemo3.ogx",
    ]

    while mayhem[guild_id]:
        interval = random.randint(MINIMUM, MAXIMUM)
        sound = random.choice(audios)
        print(
            f"{time.strftime('%H:%M:%S')} Next sound: {interval} seconds, next audio: {sound}"
        )
        await asyncio.sleep(interval)
        if vc.is_connected() and not vc.is_playing():
            vc.play(discord.FFmpegPCMAudio(sound))


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
                looping[guild_id] = []
                start_times[guild_id] = []
                mayhem[guild_id] = None
                searching[guild_id] = False
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
            if duration - elapsed < -5:  # jos biisin ois pitäny jo loppua
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


# / silence komento:
@bot.tree.command(name="hiljaisuus", description="hiljaista")
async def silence_command(interaction: discord.Interaction):
    if not await connectVoice(interaction, True):
        return
    if interaction.guild.id not in mayhem:
        mayhem[interaction.guild.id] = True
        await interaction.response.send_message(
            f"nyt on 'hiljaista' ;)", ephemeral=True
        )
    elif mayhem[interaction.guild.id] == False:
        await interaction.response.send_message(
            f"nyt on 'hiljaista' ;)", ephemeral=True
        )
        mayhem[interaction.guild.id] = True
    else:
        await interaction.response.send_message(
            f"'hiljaisuus' ohi, palataan hiljaisuuteen", ephemeral=True
        )
        mayhem[interaction.guild.id] = False
    await randomsound(interaction)


# /nimi komento:
@bot.tree.command(name="nimi", description="vaiha jonku nimi")
@app_commands.describe(target="kenen nimi vaihetaan", new_name="mikä nimi annetaan")
async def name_command(
    interaction: discord.Interaction, target: discord.Member, new_name: str
):
    if not interaction.user.guild_permissions.manage_nicknames:
        await interaction.response.send_message(
            "sulta puuttuu permissionit vaihella nimiä", ephemeral=True
        )
        return

    try:
        await target.edit(nick=new_name)
        await interaction.response.send_message(
            f"vaihettiin {target.mention}:n nimeksi **{new_name}**", ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            f"tällä tyypillä ({target.mention}) on bottia kovemmat permissionit, sen nimeä ei voi vaihtaa",
            ephemeral=True,
        )
    except Exception as e:
        await interaction.response.send_message(
            f"virhe: exception {e}", ephemeral=False
        )


# /liity komento:
@bot.tree.command(name="liity", description="liity kanavalle")
async def join(interaction: discord.Interaction):
    if not await connectVoice(interaction, True):
        return
    await interaction.response.send_message("liitytty kanavalle")


# /soitaseuraavaks
@bot.tree.command(name="soitanext", description="laita biisi seuraavaks jonnoon")
@app_commands.describe(query="biisin nimi tai youtube url")
async def playnext(interaction: discord.Interaction, query: str):
    guild_id = interaction.guild.id
    if searching.get(guild_id):
        if searching[guild_id]:
            await interaction.response.send_message(
                "dawg hold your horses there, liian nopeita inputteja", ephemeral=True
            )
            return
    try:
        if not await connectVoice(interaction, True):
            return
        searching[guild_id] = True
        await interaction.response.send_message(
            f"etitään youtubesta **{query}**", ephemeral=True
        )
        # Spotify link support
        if SPOTIFY_TRACK_REGEX.match(query):
            # Single track
            spotify_query = spotify_to_query(query)
            if not spotify_query:
                searching[guild_id] = False
                await interaction.followup.send(
                    "Spotify linkin lukeminen epäonnistui", ephemeral=True
                )
                return
            query = spotify_query

        elif SPOTIFY_PLAYLIST_REGEX.match(query):
            # Playlist / album
            tracks = spotify_playlist_to_queries(query)
            if not tracks:
                searching[guild_id] = False
                await interaction.followup.send(
                    "Spotify playlistin lukeminen epäonnistui", ephemeral=True
                )
                return

            searching[guild_id] = False
            await interaction.followup.send(
                f"Lisätään {len(tracks)} kappaletta Spotify playlististä...",
                ephemeral=True,
            )
            if len(tracks) == 50:
                await interaction.followup.send(
                    f"Playlistin maksimikoko on 50 kappaletta, tää on temporary limit",
                    ephemeral=True,
                )

            # Start a background task to enqueue tracks gradually
            bot.loop.create_task(enqueue_spotify_tracks(interaction, tracks))
            return
        url, title, duration, success = ytdlp_find(interaction, query)

        # if all else fails
        if not success:
            searching[guild_id] = False
            await interaction.followup.send(
                "age restricted video tai joku muu error tapahtu, unable to can",
                ephemeral=True,
            )
            return

        if url == None or title == None:
            await interaction.followup.send(
                f"query failed sanitization", ephemeral=True
            )

        if guild_id not in queues:
            queues[guild_id] = []

        vc = interaction.guild.voice_client
        if not vc.is_playing():
            bot.loop.create_task(check_voice_channel_empty(interaction, vc))
            # bot.loop.create_task(checkqueue_vc(interaction, vc))
            await songinfo(interaction, title, duration)
            searching[guild_id] = False
            await play_track(interaction, url, title, duration)
        else:
            queues[guild_id].insert(0, (url, title, duration))
            searching[guild_id] = False
            await songinfo(interaction, title, duration, False, True)
    except:
        return


# /soita komento
@bot.tree.command(name="soita", description="soita musiikkia youtubesta")
@app_commands.describe(query="biisin nimi tai youtube url")
async def play(interaction: discord.Interaction, query: str):
    guild_id = interaction.guild.id
    if searching.get(guild_id):
        if searching[guild_id]:
            await interaction.response.send_message(
                "dawg hold your horses there, liian nopeita inputteja", ephemeral=True
            )
            return
    try:
        if not await connectVoice(interaction, True):
            return
        searching[guild_id] = True
        await interaction.response.send_message(
            f"etitään youtubesta **{query}**", ephemeral=True
        )
        # Spotify link support
        if SPOTIFY_TRACK_REGEX.match(query):
            # Single track
            spotify_query = spotify_to_query(query)
            if not spotify_query:
                searching[guild_id] = False
                await interaction.followup.send(
                    "Spotify linkin lukeminen epäonnistui", ephemeral=True
                )
                return
            query = spotify_query

        elif SPOTIFY_PLAYLIST_REGEX.match(query):
            # Playlist / album
            tracks = spotify_playlist_to_queries(query)
            if not tracks:
                searching[guild_id] = False
                await interaction.followup.send(
                    "Spotify playlistin lukeminen epäonnistui", ephemeral=True
                )
                return

            searching[guild_id] = False
            await interaction.followup.send(
                f"Lisätään {len(tracks)} kappaletta Spotify playlististä...",
                ephemeral=True,
            )
            if len(tracks) == 50:
                await interaction.followup.send(
                    f"Playlistin maksimikoko on 50 kappaletta, tää on temporary limit",
                    ephemeral=True,
                )

            # Start a background task to enqueue tracks gradually
            bot.loop.create_task(enqueue_spotify_tracks(interaction, tracks))
            return
        url, title, duration, success = ytdlp_find(interaction, query)

        # if all else fails
        if not success:
            searching[guild_id] = False
            await interaction.followup.send(
                "age restricted video tai joku muu error tapahtu, unable to can",
                ephemeral=True,
            )
            return

        if url == None or title == None:
            await interaction.followup.send(
                f"query failed sanitization", ephemeral=True
            )

        if guild_id not in queues:
            queues[guild_id] = []

        vc = interaction.guild.voice_client
        if not vc.is_playing():
            bot.loop.create_task(check_voice_channel_empty(interaction, vc))
            # bot.loop.create_task(checkqueue_vc(interaction, vc))
            await songinfo(interaction, title, duration)
            searching[guild_id] = False
            await play_track(interaction, url, title, duration)
        else:
            queues[guild_id].append((url, title, duration))
            searching[guild_id] = False
            await songinfo(interaction, title, duration, False)
    except:
        return


# /jono komento
@bot.tree.command(name="jono", description="näytä jono")
async def show_queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id not in queues or not queues[guild_id]:
        await interaction.response.send_message("jono näyttää tyhjältä", ephemeral=True)
        return
    queue_list = ""
    if looping.get(guild_id):
        for entry in looping[guild_id]:
            queue_list += f"\n{entry[1]}"
    else:
        for entry in queues[guild_id]:
            queue_list += f"\n{entry[1]}"
    await interaction.response.send_message("done", ephemeral=True)
    await sendtochannel(
        interaction,
        f"Nyt soi: **{current_track[guild_id][1]}**\n\nSeuraavaksi jonossa:**{queue_list}**",
    )


# /skipp komento
@bot.tree.command(name="skipp", description="skippaa soiva biisi")
async def skip(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await interaction.response.send_message(
            "skipattiin seuraavaan kappaleeseen", ephemeral=False
        )
    else:
        await interaction.response.send_message("ei skipattavaa", ephemeral=True)


# /loop komento
@bot.tree.command(name="loop", description="loop moodi toggler")
@app_commands.describe(mode="pois(x), biisi(b) tai jono(q)")
async def loop(interaction: discord.Interaction, mode: str):
    guild_id = interaction.guild.id
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        await interaction.response.send_message("en oo kanavalla tai mitään ei soi")
        return

    if mode[0].lower() not in "pxbjq123":
        await interaction.response.send_message("incompat moodi")
        return

    moodi = mode[0].lower()

    if moodi in "px1":
        looping[guild_id] = []
        temp = "looppaus pois päältä"
    elif moodi in "jq3":
        looping[guild_id] = []
        for biisi in queues[guild_id]:
            looping[guild_id].append((biisi))
        looping[guild_id].append((current_track[guild_id]))
        temp = "jonon looppaus päällä"
    else:
        looping[guild_id] = []
        looping[guild_id].append((current_track[guild_id]))
        temp = "biisin looppaus päällä"

    await interaction.response.send_message("done", ephemeral=True)
    await sendtochannel(interaction, temp)


# /lopeta komennot, näitä on huvikseen monta
@bot.tree.command(name="lopeta", description="heihei botti")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    guild_id = interaction.guild.id
    if vc:
        filters[guild_id] = []
        queues[guild_id] = []
        current_track[guild_id] = []
        start_times[guild_id] = []
        looping[guild_id] = []
        mayhem[guild_id] = False
        searching[guild_id] = False
        vc.stop()
        await vc.disconnect()
        await interaction.response.send_message("poistuttu kanavalta", ephemeral=False)
    else:
        await interaction.response.send_message(
            "eioo mitään mistä poistua", ephemeral=True
        )


@bot.tree.command(name="poistu", description="heihei botti")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    guild_id = interaction.guild.id
    if vc:
        filters[guild_id] = []
        queues[guild_id] = []
        current_track[guild_id] = []
        start_times[guild_id] = []
        looping[guild_id] = []
        mayhem[guild_id] = False
        searching[guild_id] = False
        vc.stop()
        await vc.disconnect()
        await interaction.response.send_message("poistuttu kanavalta", ephemeral=False)
    else:
        await interaction.response.send_message(
            "eioo mitään mistä poistua", ephemeral=True
        )


@bot.tree.command(name="bye", description="heihei botti")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    guild_id = interaction.guild.id
    if vc:
        filters[guild_id] = []
        queues[guild_id] = []
        current_track[guild_id] = []
        start_times[guild_id] = []
        looping[guild_id] = []
        mayhem[guild_id] = False
        searching[guild_id] = False
        vc.stop()
        await vc.disconnect()
        await interaction.response.send_message("poistuttu kanavalta", ephemeral=False)
    else:
        await interaction.response.send_message(
            "eioo mitään mistä poistua", ephemeral=True
        )


# /shuffle komento jonolle
@bot.tree.command(name="shuffle", description="shufflaa jono")
async def shuffle(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    if looping.get(guild_id):
        if len(looping[guild_id]) > 1:
            random.shuffle(looping[guild_id])
            await interaction.response.send_message("loop shufflattu")
        else:
            await interaction.response.send_message("emmää voi yhtä biisiä shufflata")
    else:
        if len(queues[guild_id]) > 1:
            random.shuffle(queues[guild_id])
            await interaction.response.send_message("jono shufflattu")
        elif len(queues[guild_id]) == 1:
            await interaction.response.send_message("emmää voi shufflaa yhtä biisiä")
        else:
            await interaction.response.send_message("ei täällä soi mitään")


# /leagueofhappiness
@bot.tree.command(name="leagueofhappiness", description="maldataan yhdessä")
async def league(interaction: discord.Interaction, are_you_sure: str):
    guild_id = interaction.guild_id
    if are_you_sure.lower() != "yes":
        await interaction.response.send_message("et ollu varma", ephemeral=True)
        return

    try:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("et oo voicessa", ephemeral=True)
            return

        channel = interaction.user.voice.channel

        if not interaction.guild.voice_client:
            try:
                await channel.connect()
            except Exception as e:
                await interaction.response.send_message(
                    "liittyminen epäonnistui, exception: {e}", ephemeral=False
                )
                return
    except:
        await interaction.response.send_message(
            "joku muu meni vituilleen", ephemeral=True
        )

    await interaction.response.send_message("livin da vida loca baby")
    url, title, duration = ytdlp_find(interaction, "livin da vida loca")

    vc = interaction.guild.voice_client
    if not vc.is_playing():
        bot.loop.create_task(check_voice_channel_empty(interaction, vc))
        await play_track(interaction, url, title, duration)
    else:
        url2, title2, duration2 = current_track[guild_id]
        queues[guild_id].insert(0, (url, title, duration))
        queues[guild_id].insert(1, (url2, title2, duration2))
        vc.stop()


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

    filters[guild_id] = [
        f for f in filters[guild_id] if not f.startswith("filtteriteksti")
    ]
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

    gain = max(-50, min(gain, 50))  # max 50 min -50

    filters[guild_id] = [f for f in filters[guild_id] if not f.startswith("equalizer=")]
    filters[guild_id].append(f"equalizer=f=40:width_type=h:width=50:g={gain}")

    if gain > 0:
        if gain == 50:
            await interaction.response.send_message(
                f"amis bassot aktivoitu", ephemeral=False
            )
        else:
            await interaction.response.send_message(
                f"bassoa nostettu {gain}dB", ephemeral=False
            )
    elif gain < 0:
        await interaction.response.send_message(
            f"bassoa madallettu {gain}dB", ephemeral=False
        )
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
        await interaction.response.send_message(
            "bass boost pois päältä", ephemeral=False
        )
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
        await interaction.response.send_message(
            "amis moodi pois päältä", ephemeral=False
        )
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
        await interaction.response.send_message(
            "nightcore moodi pois päältä", ephemeral=False
        )
    else:
        filters[guild_id].append(filtteri)
        await interaction.response.send_message(
            "nightcore moodi päällä", ephemeral=False
        )

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
        await interaction.response.send_message(
            "sigma moodi pois päältä", ephemeral=False
        )
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


# --------- #
# Peikkoilu #
# --------- #


@bot.tree.command(name="gnome", description="gnome")
@app_commands.describe(target="kuka")
async def gnomeUser(interaction: discord.Interaction, target: discord.Member):
    if not target.voice or not target.voice.channel:
        await interaction.response.send_message("target ei oo voicessa", ephemeral=True)
        return

    guild_id = interaction.guild_id
    if current_track.get(guild_id):
        await interaction.response.send_message("soitan jo toisella kanavalla")
        return

    channel = target.voice.channel

    if not interaction.guild.voice_client:
        try:
            await channel.connect()
            await interaction.response.send_message("gnoming", ephemeral=True)
            vc = interaction.guild.voice_client
            vc.play(discord.FFmpegPCMAudio("/home/rottabotti/audio/gnomed.mp3"))
            await asyncio.sleep(15.5)
            await vc.disconnect()
        except Exception as e:
            await interaction.response.send_message(
                "liittyminen epäonnistu, exception: {e}"
            )
            return
    else:
        await interaction.response.send_message("failed", ephemeral=True)


# --------------- #
# muita komentoja #
# --------------- #

# none yet


# config utility funktiot
def load_channels():
    try:
        with open(CHANNEL_FILE, "r") as f:
            data = json.load(f)
        save_channels(dict(data))
        print(f"kanavat ladattu, dict: {dict(data)}")
        return dict(data)
    except Exception as e:
        print(f"virhe kanavien latauksessa: {e}")
        return {}  # tyhjä kirjasto jos ei oo olemassa


def save_channels(data):
    with open(CHANNEL_FILE, "w") as f:
        json.dump(data, f, indent=4)


# tärkee apufunktio, tän kautta laitetaan kaikki viestit kanavalle
async def sendtochannel(ctx, message: str):
    guild_id = str(ctx.guild_id)
    if guild_id not in channels:
        print("default kanavaa eioo määritetty")
        return

    channel_id = channels[guild_id]
    channel = bot.get_channel(channel_id)

    if channel:
        await channel.send(message)
    else:
        await print("epäonnistuttu viestin lähettämisessä")


@bot.tree.command(
    name="configchannel", description="konfiguroi kanava johon viestit laitetaan"
)
@app_commands.describe(channel="valitte kanava")
@commands.has_permissions(administrator=True)
async def setchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    guild_id = interaction.guild_id
    channels[guild_id] = channel.id
    save_channels(channels)
    await interaction.response.send_message(f"Kanavaksi vaihdettu {channel.mention}")


CHANNEL_FILE = "/home/rottabotti/channelconfig.json"
TOKEN_FILE = "/opt/rottabotti/.env"

channels = load_channels()  # {guild_id: channel_id}, ladataan apufunktion kautta


# botti päälle
with open(TOKEN_FILE, "r") as tokenfile:
    TOKEN = tokenfile.read().strip()

bot.run(TOKEN)
