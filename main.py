import sys
import threading

from discord.ext import commands

from discord_bot import SpoofyBot

import utils
import webapp
from security import EncryptionTool

DEFAULT_CONFIG = {
    "prefix": "s!",
    "bot_token": "INSERT_BOT_TOKEN_HERE",
    "encryption_key_passphrase": "",
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "spotify_redirect_uri": "https://spoofy.baka.tokyo/callback/",
    "spotify_scopes": "user-read-playback-state user-modify-playback-state user-read-currently-playing",
    "spotify_redirect_uri_playlist": "https://spoofy.baka.tokyo/callback_playlist/",
    "spotify_scopes_playlist": "playlist-modify-public playlist-read-collaborative",
    "spotify_connect_name": "Spoofy Bot",
    "http_host": "127.0.0.1",
    "http_port": 5000
}

if __name__ == '__main__':
    try:
        config = utils.load_config()
    except FileNotFoundError:
        config = DEFAULT_CONFIG.copy()
        utils.save_config(config)

    # If discord bot token is not present, error out and quit as we cannot do anything useful.
    if 'bot_token' not in config.keys() or config['bot_token'] in ["", DEFAULT_CONFIG['bot_token']]:
        config['bot_token'] = DEFAULT_CONFIG['bot_token']
        utils.save_config(config)
        print("Please configure the bot_token by modifying the 'config.json' file!", flush=True)
        sys.exit(1)
    bot_token = config['bot_token']

    # If the encryption key is missing, generate it and save it to the config.
    if 'encryption_key_passphrase' not in config.keys() or config['encryption_key_passphrase'] == "":
        print(f"Setting up encryption suite for the first time...")
        config['encryption_key_passphrase'] = EncryptionTool.generate().decode("utf-8")
        utils.save_config(config)
    key = config['encryption_key_passphrase'].encode("utf-8")

    # Check for other non-existent config keys and initialize them
    save = False
    if "spotify_client_id" not in config.keys():
        config["spotify_client_id"] = DEFAULT_CONFIG["spotify_client_id"]
        save = True
    if "spotify_client_secret" not in config.keys():
        config["spotify_client_secret"] = DEFAULT_CONFIG["spotify_client_secret"]
        save = True
    if "spotify_redirect_uri" not in config.keys():
        config["spotify_redirect_uri"] = DEFAULT_CONFIG["spotify_redirect_uri"]
        save = True
    if "spotify_scopes" not in config.keys():
        config["spotify_scopes"] = DEFAULT_CONFIG["spotify_scopes"]
        save = True

    # Save config if necessary
    if save:
        utils.save_config(config)

    # Ensure we have a valid encryption suite to use
    try:
        e = EncryptionTool(key)
    except EncryptionTool.InvalidKey:
        print(f"Encryption key is invalid")
        sys.exit(1)

    if 'prefix' not in config.keys() or config['prefix'] == "":
        config['prefix'] = DEFAULT_CONFIG['prefix']
        utils.save_config(config)

    prefix = config['prefix']
    print(f"Bot prefix is '{prefix}'", flush=True)

    webapp_thread = threading.Thread(target=webapp.app.run, kwargs={'host': config['http_port'],
                                                                    'port': config['http_host']})
    webapp_thread.start()

    client = commands.Bot(command_prefix=commands.when_mentioned_or(prefix))
    client.add_cog(SpoofyBot(client=client, config=config))

    webapp.app.discord_bot = client

    client.run(bot_token)

    webapp_thread.join()
