import ctypes
import json
import os
import random
import socket
import textwrap
import threading
import uuid
from threading import Thread
from typing import List

import spotipy
from spotipy.oauth2 import SpotifyOAuth, logger, SpotifyOauthError

from db import get_spotify_token_info, add_or_update_spotify_token_info, get_setting, get_spotify_username
from utils import load_config

SAMPLE_RATE = 44100
CHANNELS = 2
BITS = 16
SAMPLE_SIZE = (SAMPLE_RATE * BITS * CHANNELS) // 8
CHUNK_SIZE = SAMPLE_SIZE // 4


class SpotifyAuthManger(SpotifyOAuth):
    def __init__(self, discord_uid, *args, **kwargs):
        self.discord_uid = discord_uid
        super(SpotifyAuthManger, self).__init__(*args, **kwargs)

    def get_cached_token(self):
        """ Gets a cached auth token
        """
        token_info = None
        try:
            token_info_string = get_spotify_token_info(self.discord_uid)
            token_info = json.loads(token_info_string)

            # if scopes don't match, then bail
            if "scope" not in token_info or not self._is_scope_subset(
                    self.scope, token_info["scope"]
            ):
                return None

            if self.is_token_expired(token_info):
                token_info = self.refresh_access_token(
                    token_info["refresh_token"]
                )
        except Exception as e:
            logger.warning(f"Couldn't read cache: {e}")

        return token_info

    def _save_token_info(self, token_info):
        try:
            add_or_update_spotify_token_info(uid=self.discord_uid, token_info=json.dumps(token_info))
        except Exception as e:
            logger.warning(f"Couldn't read cache: {e}")

    # Override interactive methods, we don't want interactivity.
    def _open_auth_url(self):
        logger.warning("Interactive function `_open_auth_url()` called but ignored.")

    def _get_auth_response_interactive(self, open_browser=False):
        logger.warning("Interactive function `_get_auth_response_interactive()` called but ignored.")
        raise SpotifyOauthError("Interactive function `_get_auth_response_interactive()` called but ignored.")

    def _get_auth_response_local_server(self, redirect_port):
        logger.warning("Interactive function `_get_auth_response_local_server()` called but ignored.")
        raise SpotifyOauthError("Interactive function `_get_auth_response_local_server()` called but ignored.")

    def get_auth_response(self, open_browser=None):
        logger.warning("Interactive function `get_auth_response()` called but ignored.")
        raise SpotifyOauthError("Interactive function `get_auth_response()` called but ignored.")


def audio_listener_thread(controller: 'SpotifyController', port: int, output_io):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.bind(("0.0.0.0", port))
    sock.listen(1)
    while controller.is_listening:
        connection, address = sock.accept()
        print(f"Incoming socket connection on port {port} from {address}")
        try:
            clear_buffer_trigger = 1
            while True:
                if clear_buffer_trigger % 60 == 0:
                    clear_buffer_trigger = 0
                    try:
                        connection.setblocking(False)
                        try:
                            while True:
                                data = connection.recv(256)
                                if len(data) == 0:
                                    raise ValueError("No data read")
                        except (socket.timeout, BlockingIOError):
                            pass  # The buffer is empty
                        except ValueError as e:
                            print(f"Client has disconnected - {e}")
                            break
                    except BlockingIOError as e:
                        print(f"Could not clear buffers - {e}")
                    finally:
                        connection.setblocking(True)
                # Read and write a 0.25 second block of audio to the output
                output_io.write(connection.recv(CHUNK_SIZE))
                clear_buffer_trigger = (clear_buffer_trigger + 1)
        except BrokenPipeError:
            print("Client app disconnected or there are connection problems.")
        finally:
            connection.close()
    sock.close()


class SpotifyController:
    _instances: List['SpotifyController'] = []

    def __init__(self, voice_channel_id: str, bitrate: int, discord_uid: str):
        self.voice_channel_id: str = voice_channel_id
        self.bitrate = bitrate
        self.server_socket = None
        self.socket_io_r = None
        self.socket_io_w = None
        self.audio_thread = None
        self.port = None
        self.link_code = str(uuid.uuid4())
        self.username = None
        self.discord_uid = discord_uid
        self.playlist = None
        self.bot_config = load_config()
        self.is_listening = False

    def get_api(self):
        return spotipy.Spotify(auth_manager=SpotifyAuthManger(
            discord_uid=self.discord_uid,
            client_id=self.bot_config['spotify_client_id'],
            client_secret=self.bot_config['spotify_client_secret'],
            redirect_uri=self.bot_config['spotify_redirect_uri'],
            scope=self.bot_config['spotify_scopes'],
        ))

    def get_playlist_api(self):
        return spotipy.Spotify(auth_manager=SpotifyAuthManger(
            discord_uid=get_setting("playlist_account_uid"),
            client_id=self.bot_config['spotify_client_id'],
            client_secret=self.bot_config['spotify_client_secret'],
            redirect_uri=self.bot_config['spotify_redirect_uri_playlist'],
            scope=self.bot_config['spotify_scopes_playlist'],
        ))

    def get_or_create_playlist(self):
        if self.playlist is not None:
            return self.playlist

        api = self.get_playlist_api()

        # Find existing playlist
        pl_name = f"Spoofy Bot {self.voice_channel_id}"
        username = get_spotify_username(get_setting("playlist_account_uid"))
        limit = 50
        curr_offset = 0
        playlist = None
        while playlist is None:
            playlists = api.user_playlists(username, limit=limit, offset=curr_offset)
            for p in playlists["items"]:
                if p["name"] == pl_name:
                    playlist = p
                    print(f"Found old playlist '{pl_name}'")
                    break
            curr_offset += limit
            if playlists["next"] is None:
                break

        if playlist is None:
            # Create playlist
            playlist = api.user_playlist_create(username, pl_name, public=True, collaborative=False)
            print(f"Created playlist '{pl_name}'")
        else:
            # Check if found playlist is collaborative, and make it collaborative if it's not.
            if (not playlist["public"]) or playlist["collaborative"]:
                api.playlist_change_details(playlist["id"], public=True, collaborative=False)
                print(f"Changed playlist '{pl_name}' to public")

        self.playlist = playlist
        return playlist

    def get_playlist_uri(self):
        return self.get_or_create_playlist()['uri']

    def get_album_tracks(self, album_id):
        api = self.get_playlist_api()
        limit = 50
        offset = 0
        nxt = 1
        tracks = []
        while nxt is not None:
            res = api.album_tracks(album_id, limit=limit, offset=offset)
            tracks.extend(res['items'])
            nxt = res['next']
            offset += limit
        return tracks

    def get_playlist_tracks(self, playlist_id):
        api = self.get_playlist_api()
        limit = 50
        offset = 0
        nxt = 1
        tracks = []
        while nxt is not None:
            res = api.playlist_items(playlist_id, limit=limit, offset=offset)
            for item in res['items']:
                tracks.append(item['track'])
            nxt = res['next']
            offset += limit
        return tracks

    def get_queue(self):
        sp = self.get_api()
        api = self.get_playlist_api()
        limit = 100
        offset = 0
        items = []
        has_next = True
        while has_next:
            data = api.playlist_items(self.get_or_create_playlist()["id"],
                                      fields="next,items(track(id,name,duration_ms,artists))",
                                      limit=limit, offset=offset)
            has_next = data['next'] is not None
            for x in data['items']:
                items.append(x['track'])
            offset += limit

        info = sp.current_playback()
        is_playing, current_index, current_progress_ms = None, None, None
        if info is not None:
            is_playing = info['is_playing']
            if info['is_playing'] and info['device']['name'] == self.bot_config['spotify_connect_name']:
                current_progress_ms = info['progress_ms']
                for i, item in enumerate(items):
                    if item['id'] == info['item']['id']:
                        current_index = i
                        break

        return items, is_playing, current_index, current_progress_ms

    def is_playing_on_bot(self):
        sp = self.get_api()
        info = sp.current_playback()
        if info is not None:
            is_playing = info['is_playing']
            is_correct_device = info['device']['name'] == self.bot_config['spotify_connect_name']
            return is_playing and is_correct_device
        else:
            return False

    def clear_playlist(self):
        api = self.get_playlist_api()
        playlist_id = self.get_or_create_playlist()['id']
        items = 1
        while items > 0:
            data = api.playlist_items(playlist_id, fields="total,items(track(id))", limit=100)
            to_remove = [x['track']['id'] for x in data['items']]
            items = data['total'] - len(to_remove)
            api.playlist_remove_all_occurrences_of_items(playlist_id, items=to_remove)

    def clear_current_track(self):
        # Clear the currently playing track
        # We do this by starting to play a single song a few ms before the end of the song
        sp = self.get_api()
        devices = sp.devices()["devices"]
        spoofy_device = [x for x in devices if x["name"] == "Spoofy Bot"]
        if len(spoofy_device) > 0:
            spoofy_device = spoofy_device[0]
        else:
            raise IndexError()

        # Start playing clearing track 200ms from the end
        try:
            sp.repeat("off")
            sp.shuffle(False)
        except spotipy.SpotifyException:
            pass
        sp.start_playback(device_id=spoofy_device["id"], uris=['spotify:track:4uLU6hMCjMI75M1A2tKUQC'],
                          position_ms=213573-2000)

    def stop_playlist_playback(self):
        # Stop playback if we are currently playing the room playlist and clear the currently playing track.
        sp = self.get_api()
        items, is_playing, current_index, current_progress_ms = self.get_queue()
        if current_index is not None:
            sp.pause_playback()
            self.clear_current_track()

    def update_playlist(self):
        # Update the playlist in the current player. This is needed because new tracks don't automatically get added.
        sp = self.get_api()

        # Get current playback position
        tracks, is_playing, current_pos, current_progress_ms = self.get_queue()
        if current_pos is not None:
            # Move progress forward just a bit, to account for network delays
            current_progress_ms += 30
            # Restart playback at current position, but with new playlist
            sp.start_playback(context_uri=self.get_playlist_uri(),
                              offset={'position': current_pos},
                              position_ms=current_progress_ms)
        elif is_playing:
            # Something is playing, but we don't know what. Bail out.
            raise IndexError("Could not find currently playing track in playlist, not updating playback.")
        # Else, we're not playing, playlist will be automatically updated when playback resumes

    def start_playback(self):
        sp = self.get_api()
        devices = sp.devices()["devices"]
        spoofy_device = [x for x in devices if x["name"] == "Spoofy Bot"]
        if len(spoofy_device) > 0:
            spoofy_device = spoofy_device[0]
        else:
            raise IndexError()

        # Start playing the bot playlist on this device
        sp.start_playback(device_id=spoofy_device["id"], context_uri=self.get_playlist_uri())

    @classmethod
    def format_artist(cls, track_info):
        return textwrap.shorten(",".join([x['name'] for x in track_info['artists']]), width=25, placeholder="...")

    @classmethod
    def format_full_title(cls, track_info):
        artist = textwrap.shorten(",".join([x['name'] for x in track_info['artists']]), width=25, placeholder="...")
        return f"{artist} - {track_info['name']}"

    @classmethod
    def format_title(cls, track_info):
        return f"{track_info['name']}"

    @classmethod
    def format_album_name(cls, track_info):
        return f"{track_info['album']['name']}"

    @classmethod
    def format_progress(cls, playback_info):
        progress_ms = playback_info['progress_ms']
        duration_ms = playback_info['item']['duration_ms']
        progress_s, duration_s = progress_ms // 1000, duration_ms // 1000
        progress_m, progress_s = divmod(progress_s, 60)
        duration_m, duration_s = divmod(duration_s, 60)
        progress_fraction = progress_ms / duration_ms
        pre = round(progress_fraction * 19)
        post = 19 - pre
        return f"{'▬' * pre}🔵{'▬' * post} {progress_m}:{progress_s:02} / {duration_m}:{duration_s:02}"

    @classmethod
    def get_instances(cls):
        return cls._instances

    @classmethod
    def get_instance(cls, voice_channel_id: str):
        for inst in cls._instances:
            if inst.voice_channel_id == voice_channel_id:
                return inst
        return None

    @classmethod
    def get_instance_by_link_code(cls, link_code: str):
        for inst in cls._instances:
            if inst.link_code == link_code:
                return inst
        return None

    @classmethod
    def remove_inst(cls, voice_channel_id):
        inst = cls.get_instance(voice_channel_id)
        if inst is not None:
            cls._instances.remove(inst)

    @classmethod
    def stop_for_channel(cls, voice_channel_id):
        inst = cls.get_instance(voice_channel_id)
        if inst is not None:
            inst.stop()

    @classmethod
    def create(cls, voice_channel_id, bitrate, discord_uid):
        # Check if no existing instance exists
        inst = cls.get_instance(voice_channel_id)
        if inst is not None:
            raise ValueError("Instance for this channel already exists!")

        inst = SpotifyController(voice_channel_id=voice_channel_id, bitrate=bitrate, discord_uid=discord_uid)
        cls._instances.append(inst)
        inst.setup_socket()
        return inst

    @classmethod
    def get_free_port(cls):
        used_ports = set(inst.port for inst in cls._instances)
        try:
            return random.choice(list(set(range(15001, 16000)).difference(used_ports)))
        except IndexError:
            raise IndexError("No free ports available!")

    def set_username(self, username):
        self.username = username

    def setup_socket(self):
        if self.audio_thread is None:
            self.port = SpotifyController.get_free_port()
            self.socket_io_r, self.socket_io_w = os.pipe()
            self.is_listening = True
            audio_thread = Thread(target=audio_listener_thread,
                                  args=[self, self.port, os.fdopen(self.socket_io_w, 'wb', buffering=0)])
            self.audio_thread = audio_thread
            audio_thread.start()
        else:
            raise ValueError("Already an audio thread running?!")

    def get_id(self):
        # returns id of the respective thread
        if hasattr(self, '_thread_id'):
            return self._thread_id
        for tid, thread in threading._active.items():
            if thread is self.audio_thread:
                return tid

    def kill_socket(self):
        # Raise an exception in the running thread, to stop it.
        thread_id = self.get_id()
        res = ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, ctypes.py_object(SystemExit))
        if res > 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, 0)
            print('Could not stop audio thread.')

    def stop(self):
        # Stop subprocess
        self.is_listening = False
        self.kill_socket()

        # Join log threads
        self.audio_thread.join(timeout=1)

        # Remove self from instance list
        SpotifyController.remove_inst(self.voice_channel_id)
