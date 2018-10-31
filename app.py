import json
import os
import random
from threading import Timer

import requests
import schedule

from config import Generic
from webkicks import Webkicks


def send_random_quote():
    webkicks.send_message(random.choice(config.quote.quotes))


def replace_tokens(template, message):
    return str.replace(template, "%USER%", message.user)


http_client = requests.Session()

with open("config.json", "r") as read_file:
    config = json.load(read_file, object_hook=Generic.from_dict)

webkicks = Webkicks(os.environ.get("WK_CHATURL"), os.environ.get("WK_USERNAME"), os.environ.get("WK_PASSWORD"))

webkicks.login()

stream_url = webkicks.get_stream_url()

try:
    chat_started = False
    schedule.every(config.quote.interval).minutes.do(send_random_quote)
    stream = webkicks.http_client.get(stream_url, stream=True)
    if stream.encoding is None:
        stream.encoding = 'utf-8'

    for line in stream.iter_lines(decode_unicode=True):

        schedule.run_pending()
        if line:
            if webkicks.Pattern.UPDATE.match(line):
                chat_started = True
            if chat_started:
                chat_message = webkicks.parse_message(line)
                if chat_message:

                    if chat_message.type == "Login":
                        print(chat_message)
                        Timer(10.0, webkicks.send_message,
                              [replace_tokens(config.greeting.hello, chat_message)]).start()

                    elif chat_message.type == "Logout":
                        pass
                    elif chat_message.has_command():
                        command = chat_message.get_command()
                        if command["command"] == "ping":
                            webkicks.send_message("Pong!")
                        else:
                            pass
except KeyboardInterrupt:
    webkicks.logout()
