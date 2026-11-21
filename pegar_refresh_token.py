"""
Run ONCE on your computer to authorize the app on Spotify and get a refresh token.

Usage:
  python pegar_refresh_token.py YOUR_CLIENT_ID YOUR_CLIENT_SECRET

First, add this exact Redirect URI to your app in the Spotify Developer Dashboard:
  http://127.0.0.1:8888/callback
"""

import base64
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

REDIRECT = "http://127.0.0.1:8888/callback"
SCOPES = " ".join([
    "user-read-playback-state", "user-modify-playback-state", "user-read-currently-playing",
    "user-read-recently-played", "user-top-read", "user-read-private",
    "user-library-read", "user-library-modify", "user-follow-read", "user-follow-modify",
    "playlist-read-private", "playlist-read-collaborative",
    "playlist-modify-public", "playlist-modify-private",
])


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    client_id, client_secret = sys.argv[1], sys.argv[2]

    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code",
        "redirect_uri": REDIRECT, "scope": SCOPES,
    })
    code = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code["value"] = q.get("code", [None])[0]
            code["error"] = q.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Done, you can close this tab and go back to the terminal.</h2>".encode())

        def log_message(self, *args):
            pass

    print("Opening your browser to authorize on Spotify...")
    print("If it doesn't open, paste this link into your browser:\n" + url + "\n")
    webbrowser.open(url)
    HTTPServer(("127.0.0.1", 8888), Handler).handle_request()

    if not code.get("value"):
        print("Authorization failed:", code.get("error"))
        sys.exit(1)

    cred = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "authorization_code", "code": code["value"], "redirect_uri": REDIRECT},
        headers={"Authorization": f"Basic {cred}"},
        timeout=15,
    )
    r.raise_for_status()
    print("\nYour SPOTIFY_REFRESH_TOKEN (keep it safe, it works like a password):\n")
    print(r.json()["refresh_token"])


if __name__ == "__main__":
    main()
