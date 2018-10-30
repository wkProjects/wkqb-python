import os

import requests

import webkicks


def parse_message(message):
    print(message)


http_client = requests.Session()

webkicks = webkicks.Webkicks(os.environ.get("WK_CHATURL"), os.environ.get("WK_USERNAME"), os.environ.get("WK_PASSWORD"))

webkicks.login()

stream_url = webkicks.get_stream_url()

try:
    stream = webkicks.http_client.get(stream_url, stream=True)
    if stream.encoding is None:
        stream.encoding = 'utf-8'

    for line in stream.iter_lines(decode_unicode=True):
        if line:
            parse_message(line)
except KeyboardInterrupt:
    webkicks.logout()
