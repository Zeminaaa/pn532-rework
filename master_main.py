import time
import gc
import machine
import network

import config
from setup_hw import Buzzer
from sync_manager import MasterSyncNode
from rfid_manager import MasterReader
from web_server import DashboardServer
from logger import get_logger

logger = get_logger(__name__)

def setup_wifi() -> network.WLAN:
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    try:
        ap.config(essid=config.WIFI_SSID, password=config.WIFI_PASS, authmode=3, channel=config.WIFI_CHANNEL)
    except Exception:
        ap.config(essid=config.WIFI_SSID, channel=config.WIFI_CHANNEL)
    
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    sta.disconnect()
    try:
        sta.config(channel=config.WIFI_CHANNEL)
    except Exception:
        pass

    deadline = time.ticks_ms() + 3000
    while not ap.active():
        if time.ticks_diff(time.ticks_ms(), deadline) > 0:
            break
        time.sleep_ms(100)

    try:
        ip = ap.ifconfig()[0]
    except Exception:
        ip = "unknown"
    logger.info(f"AP started: {config.WIFI_SSID} (IP: {ip}, ch: {config.WIFI_CHANNEL})")
    return ap

def main() -> None:
    logger.info('==========================================')
    logger.info('  ORIENTEERING MASTER STATION ')
    logger.info('==========================================')

    state = {
        'mode': 'IDLE',
        'num_stations': 0,
        'synced_ids': set(),
        'chip_readings': [],
        'log': [],
    }

    def _log(msg: str) -> None:
        logger.info(msg)
        state['log'].append(msg)
        if len(state['log']) > 200:
            del state['log'][:50]

    buzzer = Buzzer()
    ap = setup_wifi()
    time.sleep_ms(300)
    reader = MasterReader(buzzer)
    sync_manager = None
    
    def get_state_cb() -> dict:
        if sync_manager:
            state['synced_ids'] = sync_manager.synced_ids.copy()
        return state

    def set_mode_cb(new_mode: str, num_stations: int) -> None:
        nonlocal sync_manager
        state['mode'] = new_mode
        state['num_stations'] = num_stations
        if new_mode == 'SYNCING':
            sync_manager = MasterSyncNode(num_stations)
            _log(f'Rezim zmenen na SYNCING ({num_stations} stanic).')
        elif new_mode == 'IDLE':
            if sync_manager:
                sync_manager.cleanup()
                sync_manager = None
            _log('Rezim zmenen na IDLE.')

    def wipe_card_cb() -> None:
        _log('Zadost o WIPE posledni karty.')
        if reader.wipe_last_card():
            _log('Karta uspesne smazana.')
            buzzer.ano_sound()
        else:
            _log('Chyba: Kartu se nepodarilo smazat.')
            buzzer.ne_sound()

    server = DashboardServer(get_state_cb, set_mode_cb, wipe_card_cb)

    _log('Master system inicializovan.')

    while True:
        try:
            server.handle_request()

            if state['mode'] == 'SYNCING' and sync_manager:
                done = sync_manager.tick()
                if done:
                    _log('Vsechny stanice synchronizovany.')
                    state['mode'] = 'READING'
                    sync_manager = None
            
            elif state['mode'] == 'READING' or state['mode'] == 'IDLE':
                result = reader.tick()
                if result:
                    _log(f"Precten cip: {result['uid']}")
                    result['id'] = len(state['chip_readings'])
                    state['chip_readings'].append(result)
                    server.sync_state(force=True)

            gc.collect()
            time.sleep_ms(10)
        except KeyboardInterrupt:
            logger.info('Bye')
            break
        except Exception as ex:
            _log(f'Chyba smycky: {ex}')

if __name__ == '__main__':
    main()
