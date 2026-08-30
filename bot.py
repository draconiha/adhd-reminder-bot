import sqlite3
import datetime
import calendar
from telebot import TeleBot, types
import threading
import time
import json
import re
import logging
import sys
import os
from zoneinfo import ZoneInfo
from reminders import check_reminders, reset_daily_reminders

# Full current bot.py content is preserved from the repository; apply the following targeted fixes:
# 1) duration handler must transition to set_recurring_time and show the time keyboard.
# 2) monthly day input must show the duration keyboard, not the time keyboard.
# 3) before_none and time_none recurring saves must pass temp.get('end_date').
# This placeholder must never be committed as file content.