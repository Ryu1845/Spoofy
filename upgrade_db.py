import os
import sqlite3


def setup_db(conn):
    with open("sql/000_db_create.sql", 'r') as f:
        script = f.read()
    c = conn.cursor()
    c.executescript(script)
    conn.commit()


def upgrade_db(conn):
    c = conn.cursor()
    db_version = int(c.execute("SELECT value FROM meta WHERE key='db_version';").fetchone()[0])
    for file in os.listdir("sql"):
        try:
            new_version = int(file.split("_")[0])
            if new_version == 0:
                continue
            required_version = new_version - 1
            if db_version == required_version:
                print(f"Upgrading db from version {db_version} to {new_version} ({file})...")
                with open(f"sql/{file}", 'r') as f:
                    script = f.read()
                c = conn.cursor()
                c.executescript(script)
                conn.commit()
            elif db_version < required_version:
                print(f"DB version ({db_version}) too low to upgrade to new version ({new_version}). "
                      f"Please check migrations and run intermediate migrations first.")
                return
            else:
                print(f"Skipping migration to version {new_version}, db already upgraded (v{db_version}).")
        except ValueError:
            print(f"File {file} not a valid db migration file.")


if __name__ == "__main__":
    conn = sqlite3.connect("db.sqlite")
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='meta';")
    if not c.fetchone():
        setup_db(conn)

    upgrade_db(conn)

    conn.commit()
    conn.close()
