# Claude Música

A voice agent for Spotify. Press your headphone button, say what you want, and Claude does it.

```
Headphones (long press) → "Hey Siri, Claude música" → you say your request
   → iPhone Shortcut → your server on Vercel → Claude decides what to do
   → Spotify API runs it → Siri reads back the answer
```

## How it works

The server gives Claude 30 tools, each wrapping a Spotify Web API endpoint (search, play, queue, playlists, library, devices and more). Claude never calls Spotify directly: it asks for a tool call, the server runs it and sends back the result, and Claude decides the next step. This loop repeats until the request is done, so a single sentence can turn into several API calls.

Spotify responses are trimmed before going back to the model (images, markets and URLs are dropped), which keeps requests fast and cheap. The default model is Claude Haiku 5.5: about 3 seconds and $0.003 per request.

## What you can ask

* Play: "play Nightcall", "play that song from the opening of Drive", "play my gym playlist on shuffle"
* Control: "pause", "skip", "go back", "restart this song", "turn on shuffle", "repeat this one"
* Queue: "queue the three most famous Radiohead songs", "what's next?"
* Playlists: "make a study playlist with 20 songs like Bonobo", "add this to playlist X", "remove this from playlist X", "rename playlist X"
* Library: "like this song", "unlike this", "follow this artist", "save this album"
* Info: "what song is this?", "what were my top songs this month?", "what did I listen to earlier?"
* Devices: "move it to my computer", "play it on the TV"
* Combos: "make a playlist with my 30 top songs of the year and play it on shuffle"

Claude replies in the language you spoke.

**Limits of the Spotify API itself** (no way around them): queue items can't be removed or reordered, and Spotify's recommendations endpoint is disabled for new apps. Claude picks similar songs from its own music knowledge and searches for each one instead.

## What you need

* Spotify Premium (required since 2026 for apps in development mode)
* An account on the [Anthropic Console](https://console.anthropic.com) with credits ($5 is enough for thousands of requests)
* A free [Vercel](https://vercel.com) account, signed in with GitHub
* Python on your computer (only for step 2)

---

## Step 1: create the Spotify app (5 min)

1. Go to [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) with your Premium account.
2. Click **Create app**.
   * App name: `Claude Musica`
   * Redirect URI: `http://127.0.0.1:8888/callback` (exactly like this, with 127.0.0.1 and not localhost)
   * Check **Web API**.
3. Save, open **Settings** and copy the **Client ID** and **Client Secret**.

## Step 2: get the refresh token (5 min)

In a terminal, inside the project folder:

```bash
pip install requests
python pegar_refresh_token.py YOUR_CLIENT_ID YOUR_CLIENT_SECRET
```

Your browser opens, you authorize, and the terminal prints the **refresh token**. Keep it safe, it works like a password.

## Step 3: create a Claude API key (3 min)

In the [Anthropic Console](https://console.anthropic.com), go to **API Keys** and create a key. Add credits under **Billing**.

## Step 4: deploy the server (10 min)

1. Fork this repository or push it to your own GitHub.
2. On Vercel: **Add New → Project**, import the repository.
3. Before clicking Deploy, open **Environment Variables** and add:

| Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | key from step 3 |
| `SPOTIFY_CLIENT_ID` | from step 1 |
| `SPOTIFY_CLIENT_SECRET` | from step 1 |
| `SPOTIFY_REFRESH_TOKEN` | from step 2 |
| `APP_SECRET` | a long password you make up (use a password generator) |
| `CLAUDE_MODEL` | optional; default is `claude-haiku-5-5`, the fastest. Switch to `claude-sonnet-5-5` for more refined music picks, at a higher cost and a bit more latency |

4. Deploy. Open `https://YOUR-PROJECT.vercel.app/` in a browser: you should see `{"status": "ok"}`.

**Test from the terminal** (with Spotify playing on some device):

```bash
curl -X POST https://YOUR-PROJECT.vercel.app/api/comando \
  -H "Authorization: Bearer YOUR_APP_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"pedido": "play Nightcall by Kavinsky"}'
```

## Step 5: create the iPhone Shortcut (5 min)

Open the **Shortcuts** app, tap **+** and add these actions in this order:

1. **Dictate Text**
   * Language: yours
   * Stop Listening: After Pause
2. **Get Contents of URL**
   * URL: `https://YOUR-PROJECT.vercel.app/api/comando`
   * Tap "Show More":
     * Method: **POST**
     * Headers: `Authorization` = `Bearer YOUR_APP_SECRET`
     * Request Body: **JSON**, with a text field `pedido` whose value is the **Dictated Text** variable
3. **Get Dictionary Value**
   * Key: `resposta` (from the "Contents of URL" dictionary)
4. **Speak Text**, using "Dictionary Value"

Optional: add an **Open App → Spotify** action right after Dictate Text, so the iPhone is visible to Spotify when the request arrives.

Name the shortcut **Claude música** (or anything you like). That name is what you say to Siri.

## How to use it

Hold your headphone button (or the stem, on AirPods) → "**Claude música**" → wait for the dictation sound → say your request.

Tip: in Settings → Siri, turn on "Allow Siri When Locked" so it works with the phone in your pocket.

---

## Troubleshooting

* **Music plays on the computer instead of the phone**: the server uses the iPhone as the default device and only plays elsewhere if you ask ("play it on the computer", "move it to the TV"). If the iPhone doesn't show up for Spotify, open the Spotify app on it once.
* **Spotify login expires**: development mode apps get a refresh token valid for 180 days. After that, run step 2 again and update `SPOTIFY_REFRESH_TOKEN` on Vercel.
* **"No active device"**: the Spotify app on the iPhone goes to sleep after a while. Open it once and try again.
* **Volume doesn't change**: iOS doesn't let external apps control the iPhone volume. It works on computers and speakers.
* **Spotify error 403**: make sure the account you authorized in step 2 is the same Premium account that owns the app.
* **Latency**: simple requests take 2 to 4 seconds; requests with many songs (a 30 track playlist) can take around 15.
* **Siri mishears the shortcut name**: rename it to something easier to say, like "DJ".

## Files

* `api/index.py`: the server (Claude loop + 30 Spotify tools)
* `pegar_refresh_token.py`: one-time Spotify login
* `vercel.json`, `requirements.txt`: deployment config
