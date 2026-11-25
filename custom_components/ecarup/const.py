from datetime import timedelta

DOMAIN = "ecarup"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"

API_BASE_URL = "https://public-api.ecarup.com"
TOKEN_URL = "https://api.smart-me.com/oauth/token"
OAUTH_SCOPE = "user.read device.read"
UPDATE_INTERVAL = timedelta(minutes=5)
HISTORY_MAX_PAGES = 100
HISTORY_BACKFILL_PAGES_PER_UPDATE = 5
HISTORY_DELTA_MAX_PAGES = 20
HISTORY_STORE_VERSION = 1
