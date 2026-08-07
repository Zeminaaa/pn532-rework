import time
import gc

from setup_hw import Buzzer, HardwareConfig
from sync_manager import SlaveSyncNode
from rfid_manager import SlaveWriter
from logger import get_logger

logger = get_logger(__name__)

def main() -> None:
    logger.info('==========================================')
    logger.info('  ORIENTEERING SLAVE STATION ')
    logger.info('==========================================')

    buzzer = Buzzer()
    station_id = HardwareConfig.get_station_id()

    logger.info(f'Station ID (raw DIP value): {station_id}')

    # BUG-C fix: validate station ID before doing anything.
    # get_station_id() returns 0-15 (raw 4-bit DIP value).
    # uprav_data() only accepts 1-8 and raises ValueError otherwise, which
    # previously caused a silent infinite loop of errors.
    if not (1 <= station_id <= 8):
        logger.error(f'KRITICKA CHYBA: Neplatne ID stanice: {station_id}. '
                     f'Ocekavano 1-8. Zkontrolujte DIP prepinace.')
        while True:
            buzzer.ne_sound()
            time.sleep_ms(1500)

    sync_node = SlaveSyncNode(station_id, buzzer)
    sync_node.wait_for_sync()
    logger.info('--- TIME SYNCED ---')

    writer = SlaveWriter(station_id, buzzer)

    logger.info('Spoustim rezim zapisu...')
    while True:
        try:
            writer.process_one_card()
            gc.collect()
        except KeyboardInterrupt:
            logger.info('Bye')
            break
        except Exception as ex:
            logger.error(f'Chyba hlavni smycky: {ex}')
            writer.recover_reader()

if __name__ == '__main__':
    main()
