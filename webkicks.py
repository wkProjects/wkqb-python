import crypt
import re
from urllib.parse import urlparse

import requests


class Webkicks:

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

    def sid_from_password(self, password):
        return crypt.crypt(password, "88")

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
