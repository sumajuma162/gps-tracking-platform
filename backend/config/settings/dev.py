from config.settings.base import *  # noqa: F403
from config.settings.env import env_bool

DEBUG = True
REFRESH_COOKIE_SECURE = env_bool("REFRESH_COOKIE_SECURE", False)  # plain-HTTP localhost
