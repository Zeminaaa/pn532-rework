"""
Shared configuration for Orienteering Stations.
"""

# Networking
WIFI_SSID: str = 'OrientacniBeh'
WIFI_PASS: str = 'start1234'
WIFI_CHANNEL: int = 1

# Hardware (SPI for PN532)
# 1 MHz is the safe upper limit for the PN532 in SPI mode.
# Matches _SPI_BAUDRATE in rfid_block4.py.
SPI_BAUDRATE: int = 1_000_000

SCK_PIN: int = 18
MOSI_PIN: int = 23
MISO_PIN: int = 19
CS_PIN: int = 5
RST_PIN: int = 17

# RFID Config
TARGET_BLOCK: int = 4
KEY: bytes = b'\xff\xff\xff\xff\xff\xff'
