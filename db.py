import sqlite3

from security import EncryptionTool
from utils import load_config


def select(query, params):
    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()
    try:
        c.execute(query, params)
        return c.fetchall()
    except sqlite3.Error as e:
        raise e
    finally:
        if c:
            c.close()


def insert(query, params):
    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()
    try:
        c.execute(query, params)
        conn.commit()
    except sqlite3.Error as e:
        raise e
    finally:
        if c:
            c.close()
    c.close()


# Delete, update and insert have the same underlying behaviour
delete = insert
update = insert


def get_setting(key):
    return select("SELECT value FROM meta WHERE key=?;", (key, ))[0][0]


def has_tokens(uid):
    return len(select("SELECT discord_uid FROM link_tokens WHERE discord_uid=?", (uid, ))) != 0


def remove_token(token):
    return delete("DELETE FROM link_tokens WHERE token=?", (token, ))


def remove_tokens(uid):
    return delete("DELETE FROM link_tokens WHERE discord_uid=?", (uid, ))


def add_token(nick, uid, token, valid_until, avatar_url):
    # Remove old tokens if they exist for this uid
    if has_tokens(uid):
        remove_tokens(uid)

    # Insert new token and return.
    return insert(
        "INSERT INTO link_tokens (discord_nick, discord_uid, token, valid_until, avatar_url) VALUES (?, ?, ?, ?, ?);",
        (nick, uid, token, valid_until, avatar_url)
    )


def get_token_info(token):
    results = select("SELECT discord_nick, discord_uid, valid_until, avatar_url "
                     "FROM link_tokens WHERE token=?", (token, ))
    return results[0] if len(results) else None


def is_linked(uid):
    return len(select(
        "SELECT discord_uid FROM spotify_details "
        "WHERE discord_uid=? AND username IS NOT NULL AND oauth_refresh IS NOT NULL",
        (uid, )
    )) != 0


def is_linked_spotify(username):
    return len(select(
        "SELECT discord_uid FROM spotify_details "
        "WHERE username=? AND oauth_refresh IS NOT NULL",
        (username, )
    )) != 0


def has_spotify_details(uid):
    return len(select("SELECT discord_uid FROM spotify_details WHERE discord_uid=?", (uid, ))) != 0


def add_or_update_spotify_details(uid, username):
    if has_spotify_details(uid):
        # Update details
        return update(
            "UPDATE spotify_details SET username=? WHERE discord_uid=?;",
            (username, uid)
        )
    else:
        # Insert new details
        return insert(
            "INSERT INTO spotify_details (discord_uid, username) VALUES (?, ?);",
            (uid, username)
        )


def add_or_update_spotify_token_info(uid, token_info):
    config = load_config()
    e = EncryptionTool(config['encryption_key_passphrase'].encode("utf-8"))
    encrypted_token_info = e.encrypt(token_info)

    if has_spotify_details(uid):
        # Update details
        return update(
            "UPDATE spotify_details SET oauth_refresh=? WHERE discord_uid=?;",
            (encrypted_token_info, uid)
        )

    else:
        # Insert new details
        return insert(
            "INSERT INTO spotify_details (discord_uid, oauth_refresh) VALUES (?, ?);",
            (uid, encrypted_token_info)
        )


def get_spotify_token_info(uid):
    config = load_config()
    e = EncryptionTool(config['encryption_key_passphrase'].encode("utf-8"))

    results = select("SELECT oauth_refresh FROM spotify_details WHERE discord_uid=?", (uid, ))
    encrypted_token_info = results[0][0] if len(results) else None
    if encrypted_token_info is not None:
        return e.decrypt(encrypted_token_info)
    return None


def remove_spotify_details(uid):
    return delete("DELETE FROM spotify_details WHERE discord_uid=?", (uid, ))


def get_spotify_username(uid):
    results = select("SELECT username FROM spotify_details WHERE discord_uid=?", (uid, ))
    details = results[0] if len(results) else None
    if details is not None:
        return details[0]
    return None
