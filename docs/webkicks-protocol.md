# The Webkicks chat protocol, as observed

Webkicks has no documentation for bots. This is what the tracer (`wkqb-tracer`) recorded on server2 in
November 2026, with normal (non-team) accounts. Names in the examples are made up.

## Session

1. `POST /<chat>/api` with `job=get_sid`, `cid`, `user` (lower case), `pass` returns `{"sid": "..."}`
   (Content-Type says ISO-8859-1, the content is ASCII). Normal accounts get `{"error": "Keine Berechtigung."}`
   for most other jobs, e.g. `get_channels`.
2. `POST /<chat>/` with the login form (`user`, `pass`, `cid`, `job=ok`, `login=Login`, `guest=`) logs in and
   returns the frameset. Chats can allow logging in directly into a room with an extra field `raum` (wkQB doesn't
   offer that; it can send `/join` instead). After 10 s the browser switches to `/<chat>/chatstream/<user>/<sid>/pullstart`
   ("Probmodus", a polling fallback) if the stream didn't work.
3. `GET /<chat>/chatstream/<User>/<sid>/start` returns a meta refresh to the stream,
   `https://serverN.webkicks.de/<chat>/chatstream/<user>/<token>/stream`. The token is new for every stream and,
   like the sid, gives access to the chat while the stream is open.
4. The stream is a never-ending HTML page (gzip, chunked, UTF-8 although no charset header is sent).

Only one session per account: a new login closes the old stream with
`<font color=red>Connection Closed (multiple Login detected)</font><br>`. A tracer or bot must not log in again
then, or both sessions throw each other out. All sessions of an account share the sid, so closing a browser that
was logged in as the account also ends the bot's session: the stream shows `<account> verl&auml;sst den Chat
<small>(Browser geschlossen)</small>` and closes. Logging in again is right then.

The server closes idle keep-alive connections; a request on such a pooled connection can fail with aiohttp's
`ServerDisconnectedError` without the server being down, so requests should be retried once.

### Nightly maintenance

Recorded on 2026-11-28 (the Perl bot expected it around 04:25):

- 04:30:01 the stream shows `<br /><font title="WebKicks.De - Sysadmin"><b><font color="#FF0000">System-Meldung:</font><span class="not_reg"> Der Chat wird aufgrund des n&auml;chtlichen Wartungszyklus f&uuml;r ca. 10 Sekunden ausfallen.</span></b><br /><br />`
  in the usual table row, without a time.
- 0.1 s later the connection drops without the end of the chunked response: aiohttp raises `ClientPayloadError`
  ("Not enough data to satisfy transfer length header"), not a normal end of the stream.
- A new login 5.5 s later worked (sid, login, stream, later keep-alives), and other accounts kept writing.
- After that login the stream showed no login line of the account itself (after `/exit` and a new login it does).

## Sending

`POST /cgi-bin/chat.cgi` with `user`, `pass=<sid>`, `cid`, `message`:

| Situation | Answer |
| --- | --- |
| message or command sent | HTTP 204, empty body |
| `/exit` | HTTP 200, page "bye, <user>" |
| account not logged in | HTTP 200, page "Fehler: Timeout. Bitte neu einloggen." |
| message field longer than 820 bytes | HTTP 200, page "Fehler: fs54SdFsA.", the message is dropped |

### Message length

Measured on 2026-11-28 with a normal account (public messages and whispers alike): `message` may have up to
**820 bytes in UTF-8**, counted over the whole field, so a whisper's `/f NAME ` counts too. An umlaut takes 2 bytes,
an emoji 4, `&` 1 (the limit is applied to the decoded form field, not to what the chat shows). 821 bytes and more
are refused as a whole, nothing is cut off. Webkicks itself speaks of 800 characters. The bot's outbox shortens
longer messages (`wkqb.outbox.fit()`), and `webkicks.send()` raises `MessageRefused` on the error page.

Errors of commands (unknown room, whisper to someone offline) arrive as Chat-Bot-PM in the stream, not in the
answer.

### Flood protection

Normal accounts are warned for sending fast: a public Chat-Bot line `1. Flooding-Verwarnung f&uuml;r <b>N</b>!`
(`title` is the warned account). According to the chat admin, the second warning comes 3 fast messages later and 3
more get the account kicked, which locks it until an admin unkicks it. Measured with distinct messages:

| Messages | Gap | Warning |
| --- | --- | --- |
| 3 | 1 s | no |
| 4 | 1 s | at the 4th |
| 4 | 1.5 s | at the 4th |
| 4 | 2 s | no |
| 3 | 3 s | no |

So the 4th message after three gaps of 1.5 s or less is warned, gaps of 2 s are fine (or: fewer than 4 messages
within about 6 s). The counter was back at 1 five minutes after a warning. A bot that isn't a chat admin must keep
at least 2.5 s between its messages, also for multi-line answers. Whether chat admins and moderators are exempt is
not known yet.

## Stream

- An HTML header (scripts, styles; can be several KB), then `<!-- update!! //-->` and
  `<div id="soundcontainer"></div>`: from here on the chat runs.
- `<!-- update //-->` every 60 seconds as keep-alive, also when nothing happens.
- Every chat event is one line: `<table border=0><tr><td valign=bottom>…</td></tr></table>\n` (sometimes followed
  by an empty line). Network chunks can hold several lines or the header and the first lines; lines must be
  buffered and split at `\n`.
- The time is `<FONT SIZE=-2>(HH:MM)</FONT>`, lower case (`<font size=-2>`) in the own login.

### Who sent it

The name is in `title="…"` and in `fluester('…')`. The visible name can be split into one `<FONT>` per letter
(colour gradient via `/col`), so it must not be parsed. Guests: `title="name (Gast)"`, `<login name />` without
the suffix. Only logins and messages show the suffix: logouts and whispers of guests look like those of
registered users.

`wkqb.events.parse_line()` turns the lines into events; `tests/fixtures/lines-*.jsonl` holds recorded lines with
the expected events.

### Lines (simplified; `T` = `<FONT SIZE=-2>(14:02)</FONT>`, `N` = the styled name)

| Event | Markup |
| --- | --- |
| message | `T <b><font title="anna"><span onclick="fluester('anna')">N</span></b>: TEXT</font>` |
| message with `/link` | as above, but `TEXT` is an `<a href=…>` and there is no `</font>` before `</td>` |
| `/me` | `T <font title="anna"><b><i><span onclick="fluester('anna')">N</span></b> TEXT</i></font>` |
| whisper to us | `T <b><span onclick="fluester('anna')">anna fl&uuml;stert</span>: <font color=red>TEXT</font>` |
| our own whisper | `T <b><span onclick="fluester('anna')">Du fl&uuml;sterst an anna</span>: <font color=red>TEXT</font>` |
| login | `T <font title="anna"><img src="/gruen.gif"><login anna /><B><i>…N…<span class="commandcolor"> betritt den Chat…` plus `<script>…rightFrame.add("anna")…` (`addp(...)` for admins; the text can contain smileys) |
| logout | `T <font title="anna"><img src="/rot.gif"><B><i>…N…<span class="commandcolor"> verlässt den Chat…` — also `verl&auml;sst den Chat.` (guests) and `… <small> (Browser geschlossen)</small>` (timeout, without `fluester`) |
| away | `T <img src="/pfeil.gif"> <b><i><font title="anna">…N…</b> meldet sich kurz ab (REASON)</i>` |
| back | `T <b><font title="anna">…N…</b> meldet sich wieder zur&uuml;ck.</i></font>` (also when an away user writes) |
| dice | `T <img src="/wuerfel.gif"> <b><i>…N…</b> w&uuml;rfelt eine <b>5</b></i>.` |
| Chat-Bot | `T <font title="anna"><b><font color="#FF0000">Chat-Bot:</font><span class="not_reg"> TEXT</b><small> (by anna)</small></span></b>` (e.g. "Neuer Moderator: …", alarm calls) |
| Chat-Bot-PM | `T <font title="Chat-Bot"><b><font color="#FF0000">Chat-Bot-PM:</font><span class="not_reg"> TEXT</span></b>` (only for us) |
| team message | `T <font title="Team-Nachricht"><b><font color="#FF0000"><span onclick="team()">Team-Nachricht</span></b> (von anna):<b></font><span class="not_reg"> TEXT</span></b>` |
| comment (`/comment`) | `T <img src="/pfeilg.gif"> <font title="anna"><b><span class="not_reg"> TEXT</span></b>` |
| room switch, seen in the old room | like a logout, plus `<span class="not_reg"> (nach <b>Smalltalk</b>)</span>` |
| room switch, seen in the new room | like a login, but without `<login …/>`, plus `<span class="not_reg"> (von <b>Hauptchat</b>)</span>` |
| room switch, own | `<span class="not_reg">Raum betreten: <b>Smalltalk</b></span>` (no time), after scripts that reload the room and online list frames (`channel.cgi?…&pass=<sid>&job=ROOM`) |
| `/w` | `<span class="not_reg"><b><u>ROOM</u></b> (3): <span onclick="fluester('anna')"><b>anna</b></span>, …</span><br><font color="red">Info: <b>fett=Administrator</b>, <i>kursiv=Moderator</i>, (G)=Gast</font>` (no time) |

### Text

Messages of normal users (not chat admins) may only contain a few tags; everything else arrives escaped
(`<em>` as `&lt;em&gt;`), so it shows as text. Measured on 2026-11-27 with a normal account:

| Kept | Escaped |
| --- | --- |
| `<b>`, `<i>`, `<u>`, `<s>`, `<small>`, `<sup>`, `<sub>`, without attributes | `<strong>`, `<em>`, `<strike>`, `<del>`, `<big>`, `<tt>`, `<code>`, `<span>`, `<br>`, `<p>`, `<div>`, `<center>`, `<h3>`, `<hr>`, `<a>`, `<img>`, `<marquee>`, `<blink>`, unknown tags |
| `<font>` with `color` (names and `#rrggbb`) and `face`, unquoted | `<font>` with quoted values or `style`; any tag with `class` or `style` |
| closing tags (also without an opening one) | |

- Tag and attribute names are lower-cased (`<FONT COLOR=green>` arrives as `<font color=green>`).
- `size` is removed from `<font>`; a `<font>` without attributes left is escaped.
- `"` and `'` become `&quot;` and `&#x27;`, which is why quoted attribute values never work.
- `&`, entities typed by the user (`&auml;`, `&lt;`) and unclosed tags (`<b>offen`) arrive unchanged.
- Umlauts and emoji arrive as UTF-8.

So the HTML of a bot that is no chat admin is limited the same way: wkQB's own `<b>` works, but custom commands
with links, images or quoted attributes show as text. Texts must be HTML-unescaped before commands are parsed or
answers compared (`wkqb.events.plain()` also removes the kept tags).

- Smileys: `<span onclick="javascript: repClick('hm')" style="cursor: pointer;"><img src="/<chat>/replacer/hm.gif"
  alt="&#58;hm"></span>` for `:hm`.
  The longest code wins (`:hangman10` is the `hangman10` smiley, not `hangman1` followed by "0"), but a code is
  also found as the start of a longer word: with a smiley `:hm`, `:hm3` shows as that smiley followed by "3". Codes
  without a smiley stay text.

### Rooms

A user is in one room at a time and only sees the messages of that room (whispers work across rooms). The main
room is `main` in URLs but `Hauptchat` for `/join` and in `/w`. `/join` takes the display name; an unknown room
gives the Chat-Bot-PM `Der Channel <b>ROOM</b> existiert nicht. Vertippt?`.

## Not observed yet

- Kicks, gags, `/cls`, messages of chat admins (HTML), flood protection for chat admins/moderators.
- Whispers to a bot by a guest, messages of a bot that is a chat admin.
- The rate limit for normal accounts.
