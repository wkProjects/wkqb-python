import logging
import re
from threading import Timer
from urllib.parse import urlparse

import requests
import schedule
from passlib.hash import des_crypt

import chatmessage
import rematcher

logger = logging.getLogger(__name__)


class Webkicks:
    class Pattern:
        CHATMESSAGE = re.compile(
            r'<FONT SIZE=-2>\((.*?)\)</FONT> '
            r'<b><font title="(.*?)">.*?: (.*?)</font></td></tr></table>')
        METEXT = re.compile(
            r'<FONT SIZE=-2>\((.*?)\)</FONT> '
            r'<font title="(.*?)"><b><i><span onclick="fluester\(\'?:.+\'\)"><b>.+</b>\s*(.+)</i>')
        WHISPERMESSAGE = re.compile(
            r'<FONT SIZE=-2>\((.*?)\)</FONT> '
            r'<b><span onclick="fluester\(\'(.*?)\'\)">.*? fl&uuml;stert</span>: '
            r'<font color=red>(.*?)</font></td></tr></table>')
        CHATBOTMESSAGE = re.compile(
            r'<FONT SIZE=-2>\((.*?)\)</FONT> '
            r'<font title=".*?"><b><font color="#FF0000">(Chat-Bot):</font>'
            r'<span class="not_reg"> (.*?)</span></b></td></tr></table>')
        CHATBOTPM = re.compile(
            r'<FONT SIZE=-2>\((.*?)\)</FONT> '
            r'<font title=".*?"><b><font color="#FF0000">(Chat-Bot-PM):</font>'
            r'<span class="not_reg"> (.*?)</span></b></td></tr>')
        LOGINMESSAGE = re.compile(
            r'<FONT SIZE=-2>\((.*?)\)</FONT> '
            r'<font title=".*?"><img src="/gruen.gif"><login ([^ ]+) />', re.IGNORECASE)
        CHANNELSWITCH = re.compile(
            r'<FONT SIZE=-2>\((.*?)\)</FONT> '
            r'<font title="(.*?)"><img src="/gruen.gif"><b><i>'
            r'<span onclick="fluester\(\'(?:\2)\'\)"><b>(.*?)</b></span>'
            r'<span class="commandcolor">.+</b></span><span class="not_reg"> \(von <b>(?:.+)</b>\)</span></i>')
        AWAY = re.compile(
            r'<FONT SIZE=-2>\((.*?)\)</FONT> '
            r'<b><font title="(.*?)"><span onclick="fluester\(\'(?:.+)\'\)">'
            r'<i><b>(.+)</b>\s*meldet sich wieder zurück.</i>')
        LOGOUTMESSAGE = re.compile(
            r'<FONT SIZE=-2>\((.*?)\)</FONT> '
            r'<font title="(.*?)"><img src=".*?/rot.gif">.*?'
            r'<span class="commandcolor"> (.*?)</b></span></i>.*?</td></tr></table>')
        REPLACER = re.compile(
            r'<span onclick="javascript: repClick\(\'(.+?)\'\)" style="cursor: pointer;">'
            r'<img src="/[_a-zA-Z0-9-]+/replacer/(.+?).(gif|jpg|png)" alt="&#58;(.+?)">'
            r'</span>')
        REBOOT = re.compile(
            r'<table border=0><tr><td valign=bottom><br />'
            r'<font title="WebKicks.De - Sysadmin"><b><font color="#FF0000">System-Meldung:</font>'
            r'<span class="not_reg"> '
            r'Der Chat wird aufgrund des nächtlichen Wartungszyklus für ca. 10 Sekunden ausfallen.'
            r'</span></b><br /><br /></td></tr>'
            r'</table>')
        UPDATE = re.compile(r'<!-- update!* //-->')
        SOUNDCONTAINER = re.compile(r'<div id="soundcontainer"></div>')
        LOGOUT = re.compile(
            r'<script language="JavaScript">'
            r'window.location.replace("https://server\d.webkicks.de/[a-zA-Z_-]+/logout");'
            r'</script>')
        COMMENT = re.compile(
            r'<FONT SIZE=-2>\((.*?)\)</FONT> <img src="/pfeilg.gif"> '
            r'<font title="(.*?)"><b><span class="not_reg"> (.*?)</span></b></td></tr></table>')

    class Type:
        CHATMESSAGE = 0
        METEXT = 1
        WHISPERMESSAGE = 2
        LOGIN = 3
        LOGOUT = 4
        CHATBOT = 5
        CHATBOTPM = 6
        REBOOT = 7
        UPDATE = 8

    def __init__(self, chat_url, **kwargs):
        self.http_client = requests.Session()
        self.http_client.headers.update({"User-Agent": "wkQB 5.0 'Python'"})

        self.username = kwargs.get("username")
        self.password = kwargs.get("password")

        chat_url_parts = urlparse(chat_url)
        self.cid = chat_url_parts.path.replace('/', '')
        self.server_url = chat_url_parts.scheme + "://" + chat_url_parts.netloc
        self.chat_url = self.server_url + '/' + self.cid
        self.sid = self.sid_from_api()
        self.send_url = self.server_url + "/cgi-bin/chat.cgi"
        self.stream_frame_url = self.chat_url + "/chatstream/" + \
            self.username + "/" + self.sid + "/start"
        self.stream_url = ''

        schedule.every(15).minutes.do(self.prevent_timeout)

    def sid_from_api(self):
        return self.http_client.post(self.chat_url + '/api', data={
            'cid': self.cid,
            'user': self.username.lower(),
            'pass': self.password,
            'job': 'get_sid'
        }).json()["sid"]

    def sid_from_password(self, password):
        return des_crypt.hash(password, salt="88")

    def login(self):
        self.http_client.post(self.chat_url + "/",
                              data={"user": self.username, "pass": self.password, "cid": self.cid, "job": "ok",
                                    "login": "Login",
                                    "guest": ""})

    def logout(self):
        self.send_message(chatmessage.Outgoing("/exit"))

    def send_message(self, message: chatmessage.Outgoing):
        # to support sending multiple messages we make sure that we have a list, even if it only contains one element
        messages = message.message if isinstance(message.message, list) else [message.message]

        for message in messages:
            self.http_client.post(self.send_url,
                                  data={"user": self.username, "pass": self.sid, "cid": self.cid,
                                        "message": message.encode("iso-8859-1", "replace")})

    def send_delayed(self, message: chatmessage.Outgoing, delay: int):
        Timer(float(delay), self.send_message, [message]).start()

    def get_stream_url(self):
        stream_frame = self.http_client.get(self.stream_frame_url)

        stream_frame_source = stream_frame.text

        stream_url_pattern = re.compile('URL=([^"]+)', re.MULTILINE)

        match = stream_url_pattern.search(stream_frame_source)
        self.stream_url = match.group(1)
        return self.stream_url

    def prevent_timeout(self):
        self.http_client.get(self.chat_url + "/tok/" + self.username.lower() + "/" + self.sid + "/")

    def parse_message(self, message):
        chat_message = None
        message = Webkicks.Pattern.REPLACER.sub(r':\1', message)
        m = rematcher.REMatcher(message)

        if m.search(Webkicks.Pattern.LOGINMESSAGE):
            chat_message = chatmessage.Incoming(m.group(2), "")
            chat_message.type = self.Type.LOGIN
        elif m.search(Webkicks.Pattern.LOGOUTMESSAGE):
            chat_message = chatmessage.Incoming(m.group(2), "")
            chat_message.type = self.Type.LOGOUT
        elif m.search(Webkicks.Pattern.CHATMESSAGE):
            chat_message = chatmessage.Incoming(m.group(2), m.group(3))
            logger.debug("Normal: " + str(chat_message))
        elif m.search(Webkicks.Pattern.WHISPERMESSAGE):
            chat_message = chatmessage.Incoming(m.group(2), m.group(3))
            logger.debug("Geflüstert: " + str(chat_message))
        elif m.search(Webkicks.Pattern.COMMENT):
            chat_message = chatmessage.Incoming(m.group(2), m.group(3))
            logger.debug("Comment: " + str(chat_message))
        elif m.search(Webkicks.Pattern.CHATBOTPM) or m.search(Webkicks.Pattern.CHATBOTMESSAGE):
            chat_message = chatmessage.Incoming(m.group(2), m.group(3))
            logger.debug("Chat-Bot: " + str(chat_message))
        elif m.search(Webkicks.Pattern.CHANNELSWITCH):
            # skipping channel switches
            pass
        elif m.search(Webkicks.Pattern.UPDATE):
            # skipping update messages
            pass
        elif m.search(Webkicks.Pattern.SOUNDCONTAINER):
            # also skipping soundcontainer
            pass
        elif m.search(Webkicks.Pattern.REBOOT):
            logger.debug("Reboot incoming!")
            logger.debug(message)
        else:
            logger.debug(message)
        return chat_message
