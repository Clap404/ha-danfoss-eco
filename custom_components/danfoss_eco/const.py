"""Constants for the Danfoss Eco integration."""

DOMAIN = "danfoss_eco"

CONF_SECRET_KEY = "secret_key"
CONF_PIN = "pin"
DEFAULT_PIN = "0000"

# The official app polls about once an hour; polling more often drains the
# AA batteries noticeably faster.
DEFAULT_SCAN_INTERVAL = 3600
