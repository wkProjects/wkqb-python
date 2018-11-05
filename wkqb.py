import json
import os
import random
import signal
import threading
import time

import schedule

from chatmessage import Outgoing
from commands import Command
from config import Generic
from webkicks import Webkicks


class WKQB:
    def __init__(self):
        self.webkicks = Webkicks(os.environ.get("WK_CHATURL"), username=os.environ.get("WK_USERNAME"),
                                 password=os.environ.get("WK_PASSWORD"))
        self.config = self.load_settings()
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

        # we prepare a stop signal for our event loop, so the bot can exit
        ev_loop_stop = threading.Event()
        for line in stream.iter_lines(decode_unicode=True):
            if line:
                if self.webkicks.Pattern.UPDATE.match(line) and not chat_started:
                    print("Chat initialized: " + time.strftime("%a, %d %b %Y %H:%M:%S"))

                    # here we can initialize some things, cause we got the first chat message
                    self.schedule_quotes()

                    # starting the event loop in a separate thread
                    ev_loop = threading.Thread(target=self.event_loop, args=[ev_loop_stop])
                    ev_loop.start()

                    chat_started = True
                if chat_started:
                    chat_message = self.webkicks.parse_message(line)
                    self.handle_message(chat_message)

        # stop the event loop so we can exit
        ev_loop_stop.set()

    def handle_message(self, chat_message):
        if chat_message:
            if chat_message.user == self.webkicks.username:
                # we dont want to react to our own messages
                return
            if self.is_ignored(chat_message.user):
                # we also ignore ignored users, obviously
                return
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
                    delay = int(command.param_string) if command.param_string is not None else 3
                    self.webkicks.send_delayed(Outgoing("Habe gewartet!"), delay)

                elif command.cmd == Command.Commands.SAY:
                    if self.is_admin(chat_message.user):
                        self.webkicks.send_message(Outgoing(command.param_string))

                elif command.cmd == Command.Commands.RELOAD:
                    if self.is_mod(chat_message.user):
                        self.load_settings()
                        self.webkicks.send_message(Outgoing("Einstellungen neu geladen!"))

                elif command.cmd == Command.Commands.QUIT:
                    if self.is_admin(chat_message.user):
                        self.webkicks.send_message(Outgoing("/exit"))

                elif command.cmd == Command.Commands.QUOTE:
                    if command.param_string:
                        if command.param_string.isdigit():
                            index = max(1, int(command.param_string)) - 1
                            if index > len(self.config.quote.quotes):
                                self.webkicks.send_message(Outgoing("So viele Zitate habe ich nicht :-("))
                            else:
                                self.webkicks.send_message(
                                    Outgoing(self.config.quote.quotes[index]))
                        else:
                            filtered_quotes = [quote for quote in self.config.quote.quotes if
                                               quote.lower().find(command.param_string.lower()) != -1]
                            if len(filtered_quotes) == 0:
                                self.webkicks.send_message(
                                    Outgoing("Kein Zitat mit '" + command.param_string + "' gefunden :-("))
                            else:
                                self.webkicks.send_message(Outgoing(random.choice(filtered_quotes)))
                    else:
                        self.send_random_quote()

                elif hasattr(self.config.commands, command.cmd):
                    response = getattr(self.config.commands, command.cmd)
                    parts = response.split("%NEW%")
                    for part in parts:
                        self.webkicks.send_message(
                            Outgoing(part, replacements={"user": chat_message.user, "random": random.random(),
                                                         "param": command.param_string}))

    def handle_user_login(self, username):
        # is there a special greeting config for the user?
        if hasattr(self.config.greeting.custom, username):
            login_config = getattr(self.config.greeting.custom, username)
        else:
            login_config = self.config.greeting

        # if the user was here recently (in the last 30 minutes) we use the welcome back greeting
        if username in self.user_list and self.user_list[username] >= time.time() - (30 * 60):
            message = getattr(login_config, "wb")
        else:
            message = getattr(login_config, "hello")

        # regardless of the greeting we use we save the timestamp of the login
        self.user_list[username] = time.time()
        greeting = Outgoing(message, replacements={"user": username})
        self.webkicks.send_delayed(greeting, 5)

    def send_random_quote(self):
        self.webkicks.send_message(Outgoing(random.choice(self.config.quote.quotes)))

    def calculate_greeting(self, username):
        if hasattr(self.config.greeting.custom, username):
            return getattr(self.config.greeting.custom, username)
        else:
            return self.config.greeting

    def schedule_quotes(self):
        schedule.every(self.config.quote.interval).minutes.do(self.send_random_quote)

    def is_ignored(self, username):
        return username in self.config.users.ignored

    def is_mod(self, username):
        return self.is_admin(username) or username in self.config.users.mods

    def is_admin(self, username):
        return self.is_master(username) or username in self.config.users.admins

    def is_master(self, username):
        return username == self.config.users.master

    def load_settings(self):
        return json.load(open("config.json", "r"), object_hook=Generic.from_dict)

    @staticmethod
    def event_loop(stop_event):
        while not stop_event.is_set():
            schedule.run_pending()
            stop_event.wait(timeout=1)
