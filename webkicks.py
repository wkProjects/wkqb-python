import re
from urllib.parse import urlparse

import requests
import schedule
from passlib.hash import des_crypt

import REMatcher
import chatmessage


class Webkicks:
    class Pattern:
        CHATMESSAGE = re.compile(
            '<FONT SIZE=-2>\((.*?)\)</FONT> <b><font title="(.*?)">.*?(): (.*?)</font></td></tr></table>')
        METEXT = re.compile(
            '<FONT SIZE=-2>\((.*?)\)</FONT> <font title="(.*?)"><b><i><span onclick="fluester\(\'(?:.+)\'\)"><b>(.+)</b>\s*(.+)</i>')
        WHISPERMESSAGE = re.compile(
            '<FONT SIZE=-2>\((.*?)\)</FONT> <b><span onclick="fluester\(\'(.*?)\'\)">.*? (fl&uuml;stert)</span>: <font color=red>(.*?)</font></td></tr></table>')
        CHATBOTMESSAGE = re.compile(
            '<FONT SIZE=-2>\((.*?)\)</FONT> <font title=".*?"><b><font color="#FF0000">(Chat-Bot)():</font><span class="not_reg"> (.*?)</span></b></font></td></tr></table>')
        CHATBOTPM = re.compile(
            '<FONT SIZE=-2>\((.*?)\)</FONT> <font title=".*?"><b><font color="#FF0000">(Chat-Bot-PM)():</font><span class="not_reg"> (.*?)</span></b></td></tr>')
        LOGINMESSAGE = re.compile(
            '<FONT SIZE=-2>\((.*?)\)</FONT> <font title=".*?"><img src="/gruen.gif"><login ([^ ]+) />', re.IGNORECASE)
        CHANNELSWITCH = re.compile(
            '<FONT SIZE=-2>\((.*?)\)</FONT> <font title="(.*?)"><img src="/gruen.gif"><b><i><span onclick="fluester\(\'(?:\2)\'\)"><b>(.*?)</b></span><span class="commandcolor">.+</b></span><span class="not_reg"> \(von <b>(?:.+)</b>\)</span></i>')
        AWAY = re.compile(
            '<FONT SIZE=-2>\((.*?)\)</FONT> <b><font title="(.*?)"><span onclick="fluester\(\'(?:.+)\'\)"><i><b>(.+)</b>\s*meldet sich wieder zurück.</i>')
        LOGOUTMESSAGE = re.compile(
            '<FONT SIZE=-2>\((.*?)\)</FONT> <font title="(.*?)"><img src=".*?/rot.gif">.*?<span class="commandcolor"> (.*?)</b></span></i>.*?</td></tr></table>')
        REPLACER = re.compile(
            '<span onclick="javascript: repClick\(\'(.+?)\'\)" style="cursor: pointer;"><img src="/[_a-zA-Z0-9-]+/replacer/(.+?).(gif|jpg|png)" alt="&#58;(.+?)"></span>')
        REBOOT = re.compile(
            '<table border=0><tr><td valign=bottom><br /><font title="WebKicks.De - Sysadmin"><b><font color="#FF0000">System-Meldung:</font><span class="not_reg"> Der Chat wird aufgrund des nächtlichen Wartungszyklus für ca 40 Sekunden ausfallen.</span></b><br /><br /></td></tr></table>')
        UPDATE = re.compile('<!-- update!* //-->')

    def __init__(self, chat_url, username, password):
        self.chat_url = chat_url
        self.username = username
        self.password = password
        self.sid = self.sid_from_password(password)

        chat_url_parts = urlparse(self.chat_url)
        self.cid = chat_url_parts.path.replace('/', '')
        self.server_url = chat_url_parts.scheme + "://" + chat_url_parts.netloc + "/"
        self.send_url = self.server_url + "/cgi-bin/chat.cgi"
        self.stream_frame_url = chat_url + "chatstream/" + self.username + "/" + self.sid + "/start"

        self.stream_url = ''
        self.http_client = requests.Session()
        self.http_client.headers.update({"User-Agent": "wkQB 5.0 'Python'"})

        schedule.every(15).minutes.do(self.prevent_timeout)

    def sid_from_password(self, password):
        return des_crypt.hash(password, salt="88")

    def login(self):
        self.http_client.post(self.chat_url,
                              data={"user": self.username, "pass": self.password, "cid": self.cid, "job": "ok",
                                    "login": "Login",
                                    "guest": ""})

    def logout(self):
        self.send_message("/exit")

    def send_message(self, message):
        self.http_client.post(self.send_url,
                              data={"user": self.username, "pass": self.sid, "cid": self.cid, "message": message})

    def get_stream_url(self):
        stream_frame = self.http_client.get(self.stream_frame_url)

        stream_frame_source = stream_frame.text

        stream_url_pattern = re.compile('URL=([^"]+)', re.MULTILINE)

        match = stream_url_pattern.search(stream_frame_source)
        self.stream_url = match.group(1)
        return self.stream_url

    def prevent_timeout(self):
        self.http_client.get(self.chat_url + "tok/" + self.username.lower() + "/" + self.sid + "/")

    def parse_message(self, message):
        chat_message = None
        m = REMatcher.REMatcher(message)
        if m.search(Webkicks.Pattern.UPDATE):
            pass
        elif m.search(Webkicks.Pattern.LOGINMESSAGE):
            chat_message = chatmessage.Message(m.group(2), "")
            chat_message.type = "Login"
        elif m.search(Webkicks.Pattern.LOGOUTMESSAGE):
            chat_message = chatmessage.Message(m.group(2), "")
            chat_message.type = "Logout"
        elif m.search(Webkicks.Pattern.CHATMESSAGE):
            chat_message = chatmessage.Message(m.group(2), m.group(4))
            print("Normal: " + str(chat_message))
        elif m.search(Webkicks.Pattern.WHISPERMESSAGE):
            chat_message = chatmessage.Message(m.group(2), m.group(4))
            print("Geflüstert: " + str(chat_message))
        else:
            print(message)
        return chat_message
