import asyncio
import re
import sys
import textwrap
import uuid
from datetime import datetime, timedelta

import discord
from discord import Embed
from discord.ext import commands
from discord.ext.commands import CommandNotFound
from discord.opus import OpusNotLoaded
from spotipy import SpotifyException

from db import add_token, get_setting, is_linked, remove_tokens, remove_spotify_details
from spotify_control import SpotifyController
from utils import init_spotify


SPOTIFY_LINK_REGEX = re.compile(r"http(s)?://open\.spotify\.com/(?P<type>[a-zA-Z]+)/(?P<id>[0-9a-zA-Z]+)")
SPOTIFY_URI_REGEX = re.compile(r"spotify:(?P<type>[a-zA-Z]+):(?P<id>[0-9a-zA-Z]+)")


class SpoofyBot(commands.Cog):
    def __init__(self, client, config, *args, **kwargs):
        super(SpoofyBot, self).__init__(*args, **kwargs)
        self.client = client
        self.bot_config = config

    @commands.Cog.listener()
    async def on_connect(self):
        print(f"Connected, preparing...", flush=True)

    @commands.Cog.listener()
    async def on_disconnect(self):
        print(f"Bot has disconnected from discord.", flush=True)

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"Bot has logged in as {self.client.user} and is ready!", flush=True)
        await self.client.change_presence(activity=discord.CustomActivity(
            name=f"Listening to {self.client.command_prefix}"
        ))

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        print(f"Joined guild {guild} ({guild.id})", flush=True)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        print(f"Left guild {guild}.", flush=True)

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if isinstance(error, CommandNotFound):
            await ctx.message.add_reaction("❔")
        raise error

    # Commands
    @commands.command()
    async def ping(self, ctx):
        """
        Test the connection of the bot.
        """
        msg_time = ctx.message.created_at
        cur_time = datetime.utcnow()
        delay = (cur_time - msg_time) / timedelta(milliseconds=1)
        await ctx.send(f"Pong! ({str(delay)} ms)")

    @commands.command()
    async def link(self, ctx):
        """
        Link a spotify account to the bot.
        """
        if not is_linked(ctx.author.id):
            token = str(uuid.uuid4())
            valid_until = int((datetime.utcnow() + timedelta(days=1)).timestamp())
            add_token(ctx.author.display_name, ctx.author.id, token, valid_until, str(ctx.author.avatar_url))
            web_base_url = get_setting('web_base_url')
            await ctx.author.send(f"Please visit {web_base_url}/link/{token} to link your Spotify account. "
                                  f"This link will expire after 24 hours.")
            if ctx.guild is not None:
                await ctx.message.add_reaction('📬')
        else:
            await ctx.reply("You have already linked a spotify account!")

    @commands.command()
    async def unlink(self, ctx):
        """
        Unlink a spotify account from the bot.
        """
        # Remove all link tokens and spotify details for this user
        remove_tokens(ctx.author.id)
        remove_spotify_details(ctx.author.id)
        await ctx.reply("All your linked accounts were removed, if you had any!")

    @commands.command()
    async def info(self, ctx):
        """
        Displays basic info about your linked spotify account (name, avatar)
        """
        if ctx.guild is not None:
            await ctx.reply("This command can only be used in DMs, because of privacy reasons.")
            raise commands.CommandError("Invoker not in DMs.")

        if not is_linked(ctx.author.id):
            await ctx.reply(f"You don't have a Spotify account linked. Please link one using "
                            f"`{self.bot_config['prefix']}link`.")
            raise commands.CommandError("User has no spotify account linked.")

        sp = init_spotify(ctx.author.id)
        result = sp.me()
        msg_embed = Embed()
        msg_embed.title = "Linked Spotify account"
        msg_embed.url = result['external_urls'].get('spotify', None)
        if len(result['images']) > 0:
            msg_embed.set_image(url=result['images'][0]['url'])
        msg_embed.add_field(name="Display name", value=result['display_name'])
        msg_embed.add_field(name="Subscription type", value=result.get('product', 'free'))
        if result.get('product', None) != "premium":
            msg_embed.add_field(name="Warning!",
                                value="Only accounts with Spotify Premium can use this bot!",
                                inline=False)
        await ctx.reply(embed=msg_embed)

    @commands.command()
    async def join(self, ctx):
        """
        Makes the bot join your voice channel
        """
        if ctx.guild is None:
            await ctx.reply("This command can only be used in a server, not in DMs.")
            raise commands.CommandError("Invoker not in a guild.")

        if not is_linked(ctx.author.id):
            await ctx.reply(f"You don't have a Spotify account linked. Please link one using "
                            f"`{self.bot_config['prefix']}link`.")
            raise commands.CommandError("User has no spotify account linked.")

        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.reply("You need to be in a voice channel to use this command.")
            raise commands.CommandError("Invoker not connected to a voice channel.")

        if ctx.voice_client is not None and ctx.author.voice.channel != ctx.voice_client.channel:
            await ctx.reply("You need to be in the same voice channel as the bot to use this command.")
            raise commands.CommandError("Invoker not in same voice channel as bot.")

        # Connect to voice channel that the invoker is in (if we're not already connected somewhere else)
        try:
            controller_instance = await ctx.author.voice.channel.connect(reconnect=False)
        except asyncio.TimeoutError:
            await ctx.reply("Timeout error while connecting to the voice channel. Please try again later.")
            return
        except discord.ClientException:
            await ctx.reply("I'm already connected to a voice channel, please disconnect me first!")
            return
        except OpusNotLoaded:
            await ctx.reply("Opus library was not loaded. Please try again later.")
            return

        if controller_instance is not None:
            # Create a listening socket for the future incoming audio connection
            try:
                controller = SpotifyController.create(controller_instance.channel.id,
                                                      controller_instance.channel.bitrate,
                                                      ctx.author.id)
            except ValueError as e:
                await ctx.reply(e)
                return

            controller.get_or_create_playlist()

            await ctx.author.send(f"Please enter the following code in your client application and click "
                                  f"'connect' to start playing music!\nCode: `{controller.link_code}`")
            await ctx.reply(f"Ready and waiting for a connection! I've DM'ed you a code to fill in in your client app."
                            f"\nIn the mean time, you can start adding songs with `{self.bot_config['prefix']}add`, "
                            f"and view the queue with `{self.bot_config['prefix']}queue`")

    @commands.command(aliases=['quit'])
    async def leave(self, ctx):
        """
        Makes the bot leave your voice channel
        """
        if ctx.guild is None:
            await ctx.reply("This command can only be used in a server, not in DMs.")
            raise commands.CommandError("Invoker not in a guild.")

        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.reply("You need to be in a voice channel to use this command.")
            raise commands.CommandError("Invoker not connected to a voice channel.")

        if ctx.voice_client is not None and ctx.author.voice.channel != ctx.voice_client.channel:
            await ctx.reply("You need to be in the same voice channel as the bot to use this command.")
            raise commands.CommandError("Invoker not in same voice channel as bot.")

        if ctx.voice_client is not None:
            SpotifyController.stop_for_channel(ctx.voice_client.channel.id)
            await ctx.voice_client.disconnect()
            return
        await ctx.send('I am not connected to a voice channel...')

    @commands.command(aliases=['a'])
    async def add(self, ctx, query):
        """
        Add a given link, spotify uri or search query to the playlist.
        """
        if ctx.guild is None:
            await ctx.reply("This command can only be used in a server, not in DMs.")
            raise commands.CommandError("Invoker not in a guild.")

        if ctx.voice_client is None or ctx.voice_client.channel is None:
            await ctx.reply(f"I am not in a voice channel, invite me first with `{self.bot_config['prefix']}join`.")
            raise commands.CommandError("Bot not connected to a voice channel.")

        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.reply("You need to be in a voice channel to use this command.")
            raise commands.CommandError("Invoker not connected to a voice channel.")

        if ctx.voice_client is not None and ctx.author.voice.channel != ctx.voice_client.channel:
            await ctx.reply("You need to be in the same voice channel as the bot to use this command.")
            raise commands.CommandError("Invoker not in same voice channel as bot.")

        if ctx.voice_client is not None and ctx.voice_client.channel is not None:
            controller = SpotifyController.get_instance(ctx.voice_client.channel.id)
            if controller is None:
                await ctx.reply(f"I'm not playing anything at the moment.")
                raise commands.CommandError("Bot not connected to active spotify session.")
        else:
            await ctx.reply(f"I am not in a voice channel, invite me first with `{self.bot_config['prefix']}join`.")
            raise commands.CommandError("Bot not connected to a voice channel.")

        print(f"Adding {query} to playlist")
        controller = SpotifyController.get_instance(ctx.voice_client.channel.id)
        sp = controller.get_playlist_api()

        uri = None
        item_info = None
        item_type = None

        # If link, queue by link
        if query.startswith("http://") or query.startswith("https://"):
            m = SPOTIFY_LINK_REGEX.match(query)
            if m:
                uri = f"spotify:{m.group('type')}:{m.group('id')}"
                item_type = m.group('type')
                if item_type == "track":
                    try:
                        item_info = sp.track(m.group('id'))
                    except SpotifyException:
                        await ctx.send(f"Cannot add! Invalid track!")
                        return
                elif item_type == "album":
                    try:
                        item_info = sp.album(m.group('id'))
                    except SpotifyException:
                        await ctx.send(f"Cannot add! Invalid album!")
                        return
                elif item_type == "playlist":
                    try:
                        item_info = sp.playlist(m.group('id'))
                    except SpotifyException:
                        await ctx.send(f"Cannot add! Invalid or private playlist!")
                        return
                else:
                    await ctx.send(f"Type {item_type} not supported!")
                    return

                print(f"Converted link to ID '{uri}'")
            else:
                await ctx.send(f"Only spotify links are supported!")
                return

        # If spotify uri, queue by link
        if uri is None:
            m = SPOTIFY_URI_REGEX.match(query)
            if m:
                uri = f"spotify:{m.group('type')}:{m.group('id')}"
                item_type = m.group('type')
                if item_type == "track":
                    try:
                        item_info = sp.track(m.group('id'))
                    except SpotifyException:
                        await ctx.send(f"Cannot add! Invalid track!")
                        return
                elif item_type == "album":
                    try:
                        item_info = sp.album(m.group('id'))
                    except SpotifyException:
                        await ctx.send(f"Cannot add! Invalid album!")
                        return
                elif item_type == "playlist":
                    try:
                        item_info = sp.playlist(m.group('id'))
                    except SpotifyException:
                        await ctx.send(f"Cannot add! Invalid or private playlist!")
                        return
                else:
                    await ctx.send(f"Type {item_type} not supported!")
                    return
                print(f"Converted URI to ID '{uri}'")

        # Else, try to search
        if uri is None:
            await ctx.send(f'Searching not supported yet.')
            return

        # Add URI
        if uri is not None:
            if item_type == "track":
                sp.playlist_add_items(controller.playlist["id"], items=[uri])
            elif item_type == "album":
                album_tracks = controller.get_album_tracks(item_info['id'])
                i, max_tracks = 0, 50
                while i < len(album_tracks):
                    block = [t['uri'] for t in album_tracks[i:i+max_tracks]]
                    sp.playlist_add_items(controller.playlist["id"], items=block)
                    i += max_tracks
            elif item_type == "playlist":
                playlist_tracks = controller.get_playlist_tracks(item_info['id'])
                i, max_tracks = 0, 50
                while i < len(playlist_tracks):
                    block = [t['uri'] for t in playlist_tracks[i:i+max_tracks]]
                    sp.playlist_add_items(controller.playlist["id"], items=block)
                    i += max_tracks
            else:
                await ctx.send(f"Cannot add! Type {item_type} not supported!")
                return

            try:
                controller.update_playlist()
            except IndexError as e:
                print(e, file=sys.stderr)

            msg_embed = Embed()
            if item_type == "track":
                full_title = SpotifyController.format_full_title(item_info)
                try:
                    thumbnail = item_info['album']['images'][0]['url']
                except IndexError:
                    thumbnail = None
                msg_embed.description = f"Added [{full_title}]({item_info['external_urls']['spotify']}) to queue!"
                msg_embed.set_thumbnail(url=thumbnail)
            elif item_type == "album":
                full_title = SpotifyController.format_full_title(item_info)
                try:
                    thumbnail = item_info['images'][0]['url']
                except IndexError:
                    thumbnail = None
                num_tracks = item_info['tracks']['total']
                msg_embed.description = f"Added album [{full_title}]({item_info['external_urls']['spotify']}) " \
                                        f"({num_tracks} tracks) to queue!"
                msg_embed.set_thumbnail(url=thumbnail)
            elif item_type == "playlist":
                title = item_info['name']
                try:
                    thumbnail = item_info['images'][0]['url']
                except IndexError:
                    thumbnail = None
                num_tracks = item_info['tracks']['total']
                msg_embed.description = f"Added playlist [{title}]({item_info['external_urls']['spotify']}) " \
                                        f"({num_tracks} tracks) to queue!"
                msg_embed.set_thumbnail(url=thumbnail)
            else:
                # Shouldn't happen, but lets add a message anyway...
                msg_embed.description = f"Unknown {item_type} item added to queue!"
            await ctx.reply(embed=msg_embed)

    @commands.command()
    async def clear(self, ctx):
        """
        Clear the room playlist
        """
        if ctx.voice_client is None or ctx.voice_client.channel is None:
            await ctx.reply(f"I am not in a voice channel, invite me first with `{self.bot_config['prefix']}join`.")
            raise commands.CommandError("Bot not connected to a voice channel.")

        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.reply("You need to be in a voice channel to use this command.")
            raise commands.CommandError("Invoker not connected to a voice channel.")

        if ctx.voice_client is not None and ctx.author.voice.channel != ctx.voice_client.channel:
            await ctx.reply("You need to be in the same voice channel as the bot to use this command.")
            raise commands.CommandError("Invoker not in same voice channel as bot.")

        controller = SpotifyController.get_instance(ctx.voice_client.channel.id)
        controller.stop_playlist_playback()
        controller.clear_playlist()
        await ctx.send(f"Queue cleared!")

    @commands.command(aliases=['q'])
    async def queue(self, ctx):
        """
        Shows the queue
        """
        if ctx.guild is None:
            await ctx.reply("This command can only be used in a server, not in DMs.")
            raise commands.CommandError("Invoker not in a guild.")

        if ctx.voice_client is None or ctx.voice_client.channel is None:
            await ctx.reply(f"I am not in a voice channel, invite me first with `{self.bot_config['prefix']}join`.")
            raise commands.CommandError("Bot not connected to a voice channel.")

        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.reply("You need to be in a voice channel to use this command.")
            raise commands.CommandError("Invoker not connected to a voice channel.")

        if ctx.voice_client is not None and ctx.author.voice.channel != ctx.voice_client.channel:
            await ctx.reply("You need to be in the same voice channel as the bot to use this command.")
            raise commands.CommandError("Invoker not in same voice channel as bot.")

        if ctx.voice_client is not None and ctx.voice_client.channel is not None:
            controller = SpotifyController.get_instance(ctx.voice_client.channel.id)
            if controller is None:
                await ctx.reply(f"I'm not playing anything at the moment.")
                raise commands.CommandError("Bot not connected to active spotify session.")
        else:
            await ctx.reply(f"I am not in a voice channel, invite me first with `{self.bot_config['prefix']}join`.")
            raise commands.CommandError("Bot not connected to a voice channel.")

        controller = SpotifyController.get_instance(ctx.voice_client.channel.id)
        queue, is_playing, current_index, current_progress_ms = controller.get_queue()

        queue_text = ""
        index_padding = len(str(len(queue)))

        # Current index none and progress not none, means we are playing on the bot, but not playing from the playlist.
        if current_index is None and current_progress_ms is not None:
            current_index = -1
            queue_text += "{-# Currently playing a custom song or playlist. #-}\n"
            queue_text += f"-- To switch back to room playlist use {self.bot_config['prefix']}start --\n\n"

            sp = controller.get_api()
            info = sp.current_playback()
            track = info['item']

            duration_ms = track['duration_ms'] - current_progress_ms
            duration_s = duration_ms // 1000
            duration_m, duration_s = divmod(duration_s, 60)
            duration = f"{duration_m}:{duration_s:02} left"
            full_title = textwrap.shorten(SpotifyController.format_full_title(track), width=64, placeholder="...")
            line = f"{full_title} {duration}"
            queue_text += f"current) {line}\n\n"

        elif current_index is None:
            current_index = -1
            if is_playing:
                queue_text += "{-# Linked spotify account is playing something elsewhere. #-}\n"
            else:
                queue_text += "{-# Currently not playing anything. #-}\n"
            queue_text += f"-- To start playing the room playlist here use {self.bot_config['prefix']}start --\n\n"


        min_index = max(current_index - 6, 0)
        max_index = min(current_index + 11, len(queue))
        more = len(queue) - max_index

        for i, track in enumerate(queue[min_index:max_index], start=min_index):
            is_current = i == current_index and is_playing
            duration_ms = track['duration_ms'] - current_progress_ms if is_current else track['duration_ms']
            duration_s = duration_ms // 1000
            duration_m, duration_s = divmod(duration_s, 60)
            duration = f"{duration_m}:{duration_s:02}{' left' if is_current else ''}"
            full_title = textwrap.shorten(SpotifyController.format_full_title(track), width=64, placeholder="...")
            line = f" {(i+1):{index_padding}d}) {full_title} {duration}\n"
            if is_current:
                line = f"{' ' * (index_padding + 4)}⬐ current track\n{line}{' ' * (index_padding + 4)}⬑ current track\n"
            queue_text += line

        if len(queue) == 0:
            queue_text += "-- Nothing in queue! --\n"
        else:
            if more > 0:
                queue_text += f"\n {' ' * index_padding} -- {more} more track(s) --\n"
            else:
                queue_text += f"\n {' ' * index_padding} -- This is the end of the queue! --\n"
                queue_text += f" {' ' * index_padding} -- Use {self.bot_config['prefix']}add to add more. --\n"

        await ctx.reply(f"```hs\n{queue_text}```")

    @commands.command(aliases=['np'])
    async def now_playing(self, ctx):
        """
        Show the currently playing song
        """
        if ctx.guild is None:
            await ctx.reply("This command can only be used in a server, not in DMs.")
            raise commands.CommandError("Invoker not in a guild.")

        if ctx.voice_client is None or ctx.voice_client.channel is None:
            await ctx.reply(f"I am not in a voice channel, invite me first with `{self.bot_config['prefix']}join`.")
            raise commands.CommandError("Bot not connected to a voice channel.")

        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.reply("You need to be in a voice channel to use this command.")
            raise commands.CommandError("Invoker not connected to a voice channel.")

        if ctx.voice_client is not None and ctx.author.voice.channel != ctx.voice_client.channel:
            await ctx.reply("You need to be in the same voice channel as the bot to use this command.")
            raise commands.CommandError("Invoker not in same voice channel as bot.")

        if ctx.voice_client is not None and ctx.voice_client.channel is not None:
            controller = SpotifyController.get_instance(ctx.voice_client.channel.id)
            if controller is None:
                await ctx.reply(f"I'm not playing anything at the moment.")
                raise commands.CommandError("Bot not connected to active spotify session.")
        else:
            await ctx.reply(f"I am not in a voice channel, invite me first with `{self.bot_config['prefix']}join`.")
            raise commands.CommandError("Bot not connected to a voice channel.")

        sp = controller.get_api()
        info = sp.current_playback()
        if not controller.is_playing_on_bot():
            await ctx.send("Not playing anything at the moment...")
            return

        # Add URI
        if info is not None:
            track_info = info['item']
            try:
                thumbnail = track_info['album']['images'][0]
            except IndexError:
                thumbnail = None
            msg_embed = Embed()
            artist_url = track_info['artists'][0]['external_urls']['spotify']
            msg_embed.set_author(name=SpotifyController.format_artist(track_info), url=artist_url)
            msg_embed.title = SpotifyController.format_title(track_info)
            msg_embed.description = f"{SpotifyController.format_album_name(track_info)}\n"
            msg_embed.url = track_info['external_urls']['spotify']
            msg_embed.set_thumbnail(url=thumbnail['url'])
            msg_embed.set_footer(text=f"{SpotifyController.format_progress(info)}")
            await ctx.reply(embed=msg_embed)
        else:
            await ctx.send("Not playing anything at the moment...")

    @commands.command()
    async def start(self, ctx):
        """
        Start playing the room playlist
        """
        if ctx.guild is None:
            await ctx.reply("This command can only be used in a server, not in DMs.")
            raise commands.CommandError("Invoker not in a guild.")

        if ctx.voice_client is None or ctx.voice_client.channel is None:
            await ctx.reply(f"I am not in a voice channel, invite me first with `{self.bot_config['prefix']}join`.")
            raise commands.CommandError("Bot not connected to a voice channel.")

        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await ctx.reply("You need to be in a voice channel to use this command.")
            raise commands.CommandError("Invoker not connected to a voice channel.")

        if ctx.voice_client is not None and ctx.author.voice.channel != ctx.voice_client.channel:
            await ctx.reply("You need to be in the same voice channel as the bot to use this command.")
            raise commands.CommandError("Invoker not in same voice channel as bot.")

        if ctx.voice_client is not None and ctx.voice_client.channel is not None:
            controller = SpotifyController.get_instance(ctx.voice_client.channel.id)
            if controller is None:
                await ctx.reply(f"I'm not playing anything at the moment.")
                raise commands.CommandError("Bot not connected to active spotify session.")
        else:
            await ctx.reply(f"I am not in a voice channel, invite me first with `{self.bot_config['prefix']}join`.")
            raise commands.CommandError("Bot not connected to a voice channel.")

        queue, is_playing, current_index, current_progress_ms = controller.get_queue()

        if current_index is not None:
            await ctx.reply(f"I'm already playing the room playlist!")
            raise commands.CommandError("Bot not connected to a voice channel.")

        controller.start_playback()
        await ctx.message.add_reaction("👍")
