import json
import os
import random
import signal
from time import time

import schedule

from chatmessage import Outgoing
from commands import Command
from config import Generic
from webkicks import Webkicks


class WKQB:
    def __init__(self):
        self.webkicks = Webkicks(os.environ.get("WK_CHATURL"), username=os.environ.get("WK_USERNAME"),
                                 password=os.environ.get("WK_PASSWORD"))
        self.config = json.load(open("config.json", "r"), object_hook=Generic.from_dict)
        self.user_list = {}
        signal.signal(signal.SIGINT, self.webkicks.logout)
        signal.signal(signal.SIGTERM, self.webkicks.logout)

    def run(self):

        self.webkicks.login()
        self.webkicks.send_delayed(Outgoing("Hallo! (wkQB 5.0, https://wkqb.de)"), 5)
        stream_url = self.webkicks.get_stream_url()

        chat_started = False

        stream = self.webkicks.http_client.get(stream_url, stream=True)
        if stream.encoding is None:
            stream.encoding = 'utf-8'

        for line in stream.iter_lines(decode_unicode=True):
            schedule.run_pending()
            if line:
                if self.webkicks.Pattern.UPDATE.match(line):
                    chat_started = True
                if chat_started:
                    chat_message = self.webkicks.parse_message(line)
                    if chat_message:
                        if chat_message.user == self.webkicks.username:
                            # we dont want to react to our own messages
                            continue

                        if chat_message.type == Webkicks.Type.LOGIN:
                            print(chat_message.user + " logged in!")
                            self.handle_user_login(chat_message.user)

                        elif chat_message.type == Webkicks.Type.LOGOUT:
                            # we dont handle logouts for now
                            pass
                        elif chat_message.has_command():

                            command = chat_message.get_command()
                            if command.cmd == Command.Commands.PING:
                                self.webkicks.send_message(Outgoing("Pong!"))
                            elif command.cmd == Command.Commands.WAIT:
                                delay = int(command["paramstring"]) if command["paramstring"] is not None else 3
                                self.webkicks.send_delayed(Outgoing("Habe gewartet!"), delay)
                            elif command.cmd == Command.Commands.SAY:
                                self.webkicks.send_message(Outgoing(command.param_string))

    def handle_user_login(self, username):
        message = self.config.greeting.hello
        if hasattr(self.config.greeting.custom, username):
            login_config = getattr(self.config.greeting.custom, username)
            if username in self.user_list:
                logintime = self.user_list[username]
                if logintime < time() - (30 * 60):
                    message = getattr(login_config, "hello")
                else:
                    message = getattr(login_config, "wb")
        self.user_list[username] = time()
        print(message)
        greeting = Outgoing(message, replacements={"user": username})
        self.webkicks.send_delayed(greeting, 5)

    def send_random_quote(self):
        self.webkicks.send_message(random.choice(self.config.quote.quotes))

    def calculate_greeting(self, username):
        if hasattr(self.config.greeting.custom, username):
            return getattr(self.config.greeting.custom, username)
        else:
            return self.config.greeting

    def schedule_quotes(self):
        schedule.every(self.config.quote.interval).minutes.do(self.send_random_quote)
