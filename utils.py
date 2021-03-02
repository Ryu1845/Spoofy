import json

import spotipy


def load_config():
    with open("config.json", "r") as f:
        return json.loads(f.read())


def save_config(config):
    json_config = json.dumps(config, indent=2)
    with open("config.json", "w") as f:
        f.write(json_config)


def init_spotify(discord_uid):
    from spotify_control import SpotifyAuthManger
    config = load_config()
    return spotipy.Spotify(auth_manager=SpotifyAuthManger(
        discord_uid=discord_uid,
        client_id=config['spotify_client_id'],
        client_secret=config['spotify_client_secret'],
        redirect_uri=config['spotify_redirect_uri'],
        scope=config['spotify_scopes'],
    ))
