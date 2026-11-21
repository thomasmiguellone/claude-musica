"""
Roda UMA vez no seu computador para autorizar o app no Spotify e obter o refresh token.

Uso:
  python pegar_refresh_token.py SEU_CLIENT_ID SEU_CLIENT_SECRET

Antes, cadastre no app do Spotify Developer o Redirect URI exato:
  http://127.0.0.1:8888/callback
"""

import base64
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

REDIRECT = "http://127.0.0.1:8888/callback"
ESCOPOS = " ".join([
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
        "redirect_uri": REDIRECT, "scope": ESCOPOS,
    })
    codigo = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            codigo["valor"] = q.get("code", [None])[0]
            codigo["erro"] = q.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Pronto, pode fechar esta aba e voltar ao terminal.</h2>".encode())

        def log_message(self, *args):
            pass

    print("Abrindo o navegador para você autorizar no Spotify...")
    print("Se não abrir sozinho, cole este link no navegador:\n" + url + "\n")
    webbrowser.open(url)
    HTTPServer(("127.0.0.1", 8888), Handler).handle_request()

    if not codigo.get("valor"):
        print("Autorização falhou:", codigo.get("erro"))
        sys.exit(1)

    cred = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "authorization_code", "code": codigo["valor"], "redirect_uri": REDIRECT},
        headers={"Authorization": f"Basic {cred}"},
        timeout=15,
    )
    r.raise_for_status()
    print("\nSeu SPOTIFY_REFRESH_TOKEN (guarde, é como uma senha):\n")
    print(r.json()["refresh_token"])


if __name__ == "__main__":
    main()
