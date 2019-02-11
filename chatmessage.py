import random
import re
from time import time, localtime, strftime
from typing import Dict

from commands import Command


class Incoming:
    def __init__(self, user, message):
        self.time = time()
        self.user = user
        self.message = message
        self.type = None
        self.from_guest = True if "(Gast)" in self.user else False
        self.from_ignored = False
        self.from_mod = False
        self.from_admin = False
        self.from_master = False

    def __str__(self):
        return "(" + strftime("%a, %d %b %Y %H:%M:%S", localtime(self.time)) + ") " + self.user + ": " + self.message

    def has_command(self):
        return re.match(r'^!\w+', self.message)

    def get_command(self):
        m = re.match(r'^!(\w+)(?: (.+))?', self.message)
        command = Command(m.group(1))
        command.param_string = m.group(2) if m.group(2) is not None else ""
        return command


class Outgoing:
    def __init__(self, message, replacements=None):
        if replacements is None:
            replacements = {}

        # in case we have a list of messages to send we need to replace tokens in all of them, so we do just that
        if isinstance(message, list):
            self.message = [self.replace_tokens(msg, replacements) for msg in message]
        else:
            self.message = self.replace_tokens(message, replacements)

    def replace_tokens(self, message, replacements: Dict[str, any]):
        # the %RANDOM% token has to be handled specially, as it wouldn't be random otherwise
        message = re.sub('%RANDOM%', str(random.random()), message, 0, re.IGNORECASE)

        # now we can replace the "normal" tokens
        for k, v in replacements.items():
            pattern = re.compile(re.escape("%" + k + "%"), re.IGNORECASE)
            message = pattern.sub(str(v), message)
        return message
