import os
import requests
from dotenv import load_dotenv

load_dotenv()
BZZOIRO_TOKEN = os.getenv("BZZOIRO_TOKEN")
url = "https://sports.bzzoiro.com/tennis/api/predictions/"
params = {"date_from": "2026-06-25", "date_to": "2026-06-27", "upcoming_only": "false", "limit": 1}
headers = {"Authorization": f"Token {BZZOIRO_TOKEN}"}
r = requests.get(url, headers=headers, params=params)
print(r.status_code)
print(r.json())
