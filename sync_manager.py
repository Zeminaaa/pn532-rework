import network
import espnow
import struct
import time
import machine
from logger import get_logger

logger = get_logger(__name__)

class MasterSyncNode:
    """Uses ESP-NOW to broadcast epoch time to slaves and collect ACKs."""

    def __init__(self, num_stations: int) -> None:
        self.num_stations = num_stations
        self.synced_ids = set()
        self.done = False
        self._last_send = 0
        self._broadcast = b'\xff' * 6

        self._e = espnow.ESPNow()
        self._e.active(True)
        try:
            self._e.add_peer(self._broadcast)
        except Exception as err:
            logger.debug(f"Failed to add broadcast peer: {err}")

        logger.info(f'MasterSyncManager init pro {num_stations} stanic.')

    def tick(self) -> bool:
        """Perform one step of synchronization. Returns True when all synced."""
        if self.done:
            return True

        current = time.time()
        if current - self._last_send >= 1.0:
            msg = struct.pack('I', int(current))
            try:
                self._e.send(self._broadcast, msg)
                t = time.localtime(current)
                logger.debug(f'Odesláno: {t[3]:02}:{t[4]:02}:{t[5]:02}')
            except OSError:
                pass
            self._last_send = current

        while True:
            try:
                host, msg = self._e.recv(0)
                if msg:
                    if msg.startswith(b'ACK'):
                        try:
                            sid = msg[3]
                            if sid not in self.synced_ids:
                                self.synced_ids.add(sid)
                                logger.info(f'SYNCED: {sid}')
                                logger.info(f'Synchronizováno: {len(self.synced_ids)}/{self.num_stations}')
                        except IndexError:
                            pass
                else:
                    break
            except OSError:
                break

        if self.num_stations > 0 and len(self.synced_ids) >= self.num_stations:
            logger.info(f'Všechny stanice ({self.num_stations}) synchronizovány. Přepínám do READ.')
            self.done = True
            self.cleanup()
            return True

        return False

    def cleanup(self) -> None:
        try:
            self._e.active(False)
        except Exception as err:
            logger.debug(f"Cleanup failed: {err}")
        logger.info('Cleanup hotov.')


class SlaveSyncNode:
    """Listens for epoch time from Master and updates RTC."""

    def __init__(self, station_id: int, buzzer) -> None:
        self.station_id = station_id
        self.buzzer = buzzer

    def wait_for_sync(self) -> None:
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        sta.disconnect()
        
        try:
            sta.config(channel=1)
        except Exception as e:
            logger.error(f"Failed to set channel: {e}")

        logger.info(f"WiFi Channel set to: {sta.config('channel')}")

        e = espnow.ESPNow()
        e.active(True)
        broadcast = b'\xff' * 6
        try:
            e.add_peer(broadcast)
        except Exception as err:
            logger.debug(f"Failed to add broadcast peer: {err}")
        
        logger.info("Čekám na signál od Mastera (ESP-NOW)...")
        is_synced = False
        
        while not is_synced:
            try:
                host, msg = e.recv(200)
                if msg:
                    logger.debug(f"Received from {host}")
                    try:
                        received_epoch = struct.unpack('I', msg)[0]
                        tm = time.localtime(received_epoch)
                        machine.RTC().datetime((tm[0], tm[1], tm[2], tm[6], tm[3], tm[4], tm[5], 0))
                        logger.info(f"ÚSPĚCH! Čas nastaven: {tm[3]:02}:{tm[4]:02}:{tm[5]:02}")
                        is_synced = True
                    except Exception as ex:
                        logger.error(f"Parse error: {ex}")
            except OSError:
                pass

        logger.info("--- SENDING ACK ---")
        try:
            msg = b'ACK' + bytes([self.station_id])
            logger.info(f"Odesílám ACK (ID {self.station_id}) 5x...")
            for _ in range(5):
                e.send(broadcast, msg)
                time.sleep(0.05)
        except Exception as err:
            logger.error(f"Chyba odeslání ACK: {err}")

        try:
            e.active(False)
            sta.active(False)
        except Exception as err:
            logger.debug(f"Failed to deactivate interfaces: {err}")
        
        logger.info("Přehrávám potvrzení...")
        try:
            self.buzzer.sync_confirm()
        except Exception as err:
            logger.error(f"Buzzer error: {err}")
            
        logger.info("Hotovo. Předávám řízení aplikaci.")
