#!/bin/bash

cd /opt/spoofy_bot
source ./venv/bin/activate
echo "Upgrading database..."
python upgrade_db.py
echo "Starting bot..."
python main.py
