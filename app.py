import json
import os
import random

import requests
import schedule

from chatmessage import Outgoing
from commands import Command
from config import Generic
from webkicks import Webkicks


def send_random_quote():
    webkicks.send_message(random.choice(config.quote.quotes))


http_client = requests.Session()

with open("config.json", "r") as read_file:
    config = json.load(read_file, object_hook=Generic.from_dict)

webkicks = Webkicks(os.environ.get("WK_CHATURL"), username=os.environ.get("WK_USERNAME"),
                    password=os.environ.get("WK_PASSWORD"))

webkicks.login()

stream_url = webkicks.get_stream_url()

try:
    chat_started = False
    # schedule.every(config.quote.interval).minutes.do(send_random_quote)
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
                    if chat_message.user == webkicks.username:
                        # we dont want to react to our own messages
                        continue

                    if chat_message.type == Webkicks.Type.LOGIN:
                        print(chat_message.user + " logged in!")
                        greeting = Outgoing(config.greeting.hello, replacements={"user": chat_message.user})
                        webkicks.send_delayed(greeting, 5)

                    elif chat_message.type == Webkicks.Type.LOGOUT:
                        # we dont handle logouts for now
                        pass
                    elif chat_message.has_command():

                        command = chat_message.get_command()
                        if command.cmd == Command.Commands.PING:
                            webkicks.send_message(Outgoing("Pong!"))
                        elif command.cmd == Command.Commands.WAIT:
                            delay = int(command["paramstring"]) if command["paramstring"] is not None else 3
                            webkicks.send_delayed(Outgoing("Habe gewartet!"), delay)
                        elif command.cmd == Command.Commands.SAY:
                            webkicks.send_message(Outgoing(command.param_string))

except KeyboardInterrupt:
    webkicks.logout()
