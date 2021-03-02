import datetime
import os
import sys
from typing import List

import discord
import spotipy
from discord.ext.commands import Bot
from flask import Flask, abort, request, render_template, redirect

from audio_converter import FFmpegSpotifyAudio
from db import get_token_info, remove_token, add_or_update_spotify_details, remove_tokens, is_linked_spotify, \
    get_setting, has_spotify_details
from spotify_control import SpotifyAuthManger, SpotifyController
from utils import load_config


app = Flask(__name__)


@app.route('/')
def hello_world():
    return 'Hello, World!'


@app.route('/link/<uuid:token>', methods=["GET", "POST"])
def link(token):
    token_info = get_token_info(str(token))
    if token_info is None:
        abort(404)

    discord_nick, discord_uid, valid_until, avatar_url = token_info

    # Check if token is still valid, else remove token and return 404
    if datetime.datetime.utcnow().timestamp() > float(valid_until):
        remove_token(str(token))
        abort(404)

    # Render form page if method is GET
    if request.method == "GET":
        return render_template('link_form.html', **{
            'nick': discord_nick, 'avatar': avatar_url, 'token': str(token)
        })

    # Redirect to Spotify oAuth login
    config = load_config()
    sp = spotipy.Spotify(auth_manager=SpotifyAuthManger(
        discord_uid=discord_uid,
        client_id=config['spotify_client_id'],
        client_secret=config['spotify_client_secret'],
        redirect_uri=config['spotify_redirect_uri'],
        scope=config['spotify_scopes']
    ))
    redirect_url = sp.auth_manager.get_authorize_url(state=token)
    return redirect(redirect_url)


@app.route('/link_playlist_account', methods=["GET", "POST"])
def link_playlist():
    playlist_uid = get_setting('playlist_account_uid')
    if has_spotify_details(playlist_uid):
        return "Already linked"

    # Redirect to Spotify oAuth login
    config = load_config()
    print(config['spotify_redirect_uri_playlist'])
    sp = spotipy.Spotify(auth_manager=SpotifyAuthManger(
        discord_uid=playlist_uid,
        client_id=config['spotify_client_id'],
        client_secret=config['spotify_client_secret'],
        redirect_uri=config['spotify_redirect_uri_playlist'],
        scope=config['spotify_scopes_playlist']
    ))
    redirect_url = sp.auth_manager.get_authorize_url(state=1)
    return redirect(redirect_url)


@app.route('/callback/', methods=["GET"])
def callback():
    code = request.args.get("code")
    token = request.args.get("state")

    token_info = get_token_info(str(token))
    if token_info is None:
        abort(404)

    discord_nick, discord_uid, valid_until, avatar_url = token_info

    # Check if token is still valid, else remove token and return 404
    if datetime.datetime.utcnow().timestamp() > float(valid_until):
        remove_token(str(token))
        abort(404)

    # Use received authorization code to get refresh token and the likes.
    config = load_config()
    sp = spotipy.Spotify(auth_manager=SpotifyAuthManger(
        discord_uid=discord_uid,
        client_id=config['spotify_client_id'],
        client_secret=config['spotify_client_secret'],
        redirect_uri=config['spotify_redirect_uri'],
        scope=config['spotify_scopes'],
    ))
    try:
        sp.auth_manager.get_access_token(code=code, check_cache=False)
    except spotipy.SpotifyOauthError as e:
        # Failed
        print(f"Failed to link - oAuth error - {e}", file=sys.stderr)
        return render_template('failed.html', **{
            'nick': discord_nick, 'avatar': avatar_url, 'token': str(token)
        })

    # Test API
    try:
        result = sp.me()

        # Save user ID to allow linking to client app later on
        add_or_update_spotify_details(discord_uid, result['id'])

        # Remove link token, we're done with it.
        remove_tokens(discord_uid)

        return render_template('success.html', **{
            'nick': discord_nick, 'avatar': avatar_url, 'info': result
        })
    except spotipy.SpotifyException as e:
        # Failed
        print(f"Failed to link - Spotify error - {e}", file=sys.stderr)
        return render_template('failed.html', **{
            'nick': discord_nick, 'avatar': avatar_url, 'token': str(token)
        })


@app.route('/callback_playlist/', methods=["GET"])
def callback_playlist():
    code = request.args.get("code")
    uid = request.args.get("state")
    playlist_uid = get_setting('playlist_account_uid')
    if uid != playlist_uid:
        return "Failed to link! Incorrect state."

    # Use received authorization code to get refresh token and the likes.
    config = load_config()
    sp = spotipy.Spotify(auth_manager=SpotifyAuthManger(
        discord_uid=playlist_uid,
        client_id=config['spotify_client_id'],
        client_secret=config['spotify_client_secret'],
        redirect_uri=config['spotify_redirect_uri_playlist'],
        scope=config['spotify_scopes_playlist'],
    ))
    try:
        sp.auth_manager.get_access_token(code=code, check_cache=False)
    except spotipy.SpotifyOauthError as e:
        # Failed
        print(f"Failed to link - oAuth error - {e}", file=sys.stderr)
        return f"Failed to link! oAuth error."

    # Test API
    try:
        result = sp.me()

        # Save user ID to allow linking to client app later on
        add_or_update_spotify_details(playlist_uid, result['id'])
        return "Linked successfully!"
    except spotipy.SpotifyException as e:
        # Failed
        print(f"Failed to link - Spotify error - {e}", file=sys.stderr)
        return "Failed to link! Spotify Exception"


@app.route('/check/', methods=["GET"])
def check():
    username = request.args.get("user")
    oauth_is_linked = is_linked_spotify(username)
    return {"linked": oauth_is_linked}


@app.route('/connect/', methods=["GET"])
def connect():
    controller = SpotifyController.get_instance_by_link_code(request.args.get("link_code"))
    if controller is not None:
        controller.set_username(request.args.get("user"))
        return {"address": get_setting("stream_host"), "port": controller.port}
    config = load_config()
    return {"error": True, "short_msg": "Invalid link code.",
            "msg": f"Invalid link code. Invite the bot to a voice channel first with '{config['prefix']}join'"}


@app.route('/start/', methods=["GET"])
def start():
    link_code = request.args.get("link_code")
    controller = SpotifyController.get_instance_by_link_code(link_code)
    if controller is not None:
        # noinspection PyUnresolvedReferences
        bot: Bot = app.discord_bot
        voice_controller: List[discord.VoiceClient] = [x for x in bot.voice_clients
                                                       if x.channel.id == controller.voice_channel_id]
        voice_controller: discord.VoiceClient = voice_controller[0] if len(voice_controller) > 0 else None
        if voice_controller is not None:
            # Switch Spotify to the bot player
            try:
                controller.clear_current_track()
                controller.start_playback()
            except IndexError:
                print("Spoofy playback device not found in Spotify. Is the client app running?")
                return {"status": "error", "error": True, "short_msg": "Device not found.",
                        "msg": "Spoofy playback device not found in Spotify. Is the client app running?"}
            except spotipy.SpotifyException as e:
                # Failed
                print(f"Failed to connect to spotify API! {e}")
                return {"status": "error", "error": True, "short_msg": "Could not switch players.",
                        "msg": "Failed to connect to Spotify API to switch players!"}

            # Start playing audio from the controller
            try:
                # If the bot is not playing, initialize a new source and start playing.
                if not voice_controller.is_playing():
                    source = discord.PCMVolumeTransformer(FFmpegSpotifyAudio(
                        os.fdopen(controller.socket_io_r, 'rb', buffering=0),
                        link_code=link_code,
                        pipe=True
                    ))
                    voice_controller.play(source, after=lambda x: print('Player error: %s' % x) if x else None)
                    return {"status": "OK"}

                # If it is playing, and it is playing from the same FFmpegSpotifyAudio source with this link code,
                # allow the client to reconnect and continue playing where it left off.
                elif isinstance(voice_controller.source, discord.PCMVolumeTransformer):
                    if isinstance(voice_controller.source.original, FFmpegSpotifyAudio):
                        if voice_controller.source.original.link_code == link_code:
                            return {"status": "OK"}

                # In any other case, this is too complex of a situation to bother to fix. Disconnect the client.
                return {"status": "error", "error": True, "short_msg": "Already playing audio.",
                        "msg": "Failed to start playback, bot is already playing audio from a different source! "
                               "If this is incorrect, reconnect the bot (s!leave, and then s!join)."}
            except discord.ClientException:
                return {"status": "error", "error": True, "short_msg": "Already playing audio.",
                        "msg": "Failed to start playback, bot is already playing audio from a different source! "
                               "If this is incorrect, reconnect the bot (s!leave, and then s!join)."}

        config = load_config()
        return {"error": True, "short_msg": "No active voice session",
                "msg": f"Could not find an active voice session. "
                       f"Invite the bot to a voice channel first with '{config['prefix']}join'"}
    config = load_config()
    return {"error": True, "short_msg": "Invalid link code.",
            "msg": f"Invalid link code. Invite the bot to a voice channel first with '{config['prefix']}join'"}
