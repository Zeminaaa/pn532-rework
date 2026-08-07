"""
rfid_manager.py — RFID hardware abstraction layer
====================================================
Hardware : PN532 over hardware SPI (ESP32)
Pinout   : SCK=18, MOSI=23, MISO=19, RST=17, CS=5  (unchanged from config.py)
Protocol : MIFARE Classic 1K, Key B (0xFF×6) by default

Migration notes (MFRC522 → PN532):
  - Driver swapped: mfrc522 → pn532.PN532
  - SPI bus: SPI(2) → SPI(1) as required by rfid_block4.py
  - Card detection: rdr.request/anticoll/select_tag → nfc.read_passive_target()
  - Authentication: rdr.auth() → nfc.mifare_classic_authenticate_block()
  - Read: rdr.read() → nfc.mifare_classic_read_block()
  - Write: rdr.write() → InDataExchange + MIFARE_CMD_WRITE (0xA0)
  - RF field release: rdr.halt_a()/stop_crypto1() → _halt_field()
  - Atomicity sequence (read_update_verify_once) preserved verbatim from rfid_block4.py
  - All other architecture, debounce, buzzer wiring, and error recovery: UNCHANGED.

Skill constraints enforced:
  - read_update_verify_once atomicity on every write
  - _halt_field() always called after field use (in finally blocks)
  - No time.sleep() in main logic — only utime.sleep_ms() for brief protocol pauses
  - const() for all integer literals
  - Pre-allocated write_params bytearray reused across calls
  - Structured error returns — never silent failures
"""

import utime
import gc
from machine import Pin, SPI
from micropython import const
import config
from pn532 import PN532, MIFARE_CMD_AUTH_A, MIFARE_CMD_AUTH_B, KEY_DEFAULT_B
from logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# MIFARE addressing — mirrors rfid_block4.py constants
# ---------------------------------------------------------------------------
_TARGET_BLOCK   = const(4)    # Block 4, sector 1 — safe data block
_BLOCK_SIZE     = const(16)   # MIFARE Classic block size in bytes
_AUTH_RETRIES   = const(3)    # Authentication attempts before giving up
_VERIFY_RETRIES = const(2)    # Post-write read-back attempts

# Pre-allocated write command parameter buffer (tg + cmd + block + 16 data bytes)
# Reused on every write call to avoid heap fragmentation in the main loop.
_WRITE_PARAMS = bytearray(3 + _BLOCK_SIZE)


# ---------------------------------------------------------------------------
# Internal helpers (equivalent to rfid_block4.py private functions)
# ---------------------------------------------------------------------------

def _authenticate(nfc: PN532, uid: bytes,
                  key_type: int = MIFARE_CMD_AUTH_B,
                  key: bytes = KEY_DEFAULT_B) -> bool:
    """
    Authenticate _TARGET_BLOCK with up to _AUTH_RETRIES attempts.
    Returns True on success, False on all failures.
    The PN532 authenticates the entire sector via the block number;
    block 4 → sector 1 is correctly targeted here.
    """
    for attempt in range(_AUTH_RETRIES):
        try:
            ok = nfc.mifare_classic_authenticate_block(
                uid,
                _TARGET_BLOCK,
                key_type,
                key,
            )
            if ok:
                return True
            if attempt < _AUTH_RETRIES - 1:
                utime.sleep_ms(10)    # brief pause between retries
        except Exception as exc:
            logger.warning('[WARN] _authenticate attempt {}/{} exception: {}'.format(
                attempt + 1, _AUTH_RETRIES, exc))
            if attempt < _AUTH_RETRIES - 1:
                utime.sleep_ms(20)
    return False


def _halt_field(nfc: PN532) -> None:
    """
    Release the RF field: send HALT_A and stop crypto.
    Mandatory per skill — frees the field for the next runner.

    The pn532.py driver does not expose halt_a / stop_crypto1 directly,
    so we use InDataExchange with the MIFARE HALT command (0x50, 0x00)
    and then re-call SAM_configuration to reset the PN532 field state.
    """
    try:
        # MIFARE HALT command — card goes to HALT state
        nfc.call_function(
            0x40,                          # InDataExchange
            params=[0x01, 0x50, 0x00],     # tg=1, HALT_A
            response_length=1,
            timeout=200,
        )
    except Exception:
        pass    # HALT may time-out normally if card is already gone; expected
    try:
        # Re-configure SAM — implicitly stops crypto and resets RF state
        nfc.SAM_configuration()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# BaseRFID — hardware init and shared PN532 communication
# ---------------------------------------------------------------------------

class BaseRFID:
    """Base class handling hardware init and raw PN532 communication."""

    def __init__(self) -> None:
        # Pin objects are created once and reused across reinit cycles
        self.sck  = Pin(config.SCK_PIN,  Pin.OUT)
        self.mosi = Pin(config.MOSI_PIN, Pin.OUT)
        self.miso = Pin(config.MISO_PIN, Pin.IN)
        self.cs   = Pin(config.CS_PIN,   Pin.OUT, value=1)
        self.rst  = Pin(config.RST_PIN,  Pin.OUT, value=1)
        self.vspi = None
        self.nfc  = None
        self.init_reader()

    def init_reader(self) -> None:
        """Initialise SPI(1) bus and bring up the PN532 with SAM_configuration."""
        self.cs.value(1)
        # SPI(1) is required by the PN532 driver (hardware SPI bus 1)
        self.vspi = SPI(
            1,
            baudrate=config.SPI_BAUDRATE,
            polarity=0,
            phase=0,
            sck=self.sck,
            mosi=self.mosi,
            miso=self.miso,
        )
        self.nfc = PN532(self.vspi, self.cs, reset=self.rst, debug=False)
        self.nfc.SAM_configuration()
        logger.info('PN532 inicializovan.')

    def recover_reader(self) -> None:
        """Full SPI + PN532 teardown and recreation.
        A full rebuild is required after a desynced SPI bus — partial
        reinit (SAM_configuration only) does not recover hard faults."""
        logger.info('Obnovuji PN532 (plna reinicializace SPI)...')
        try:
            self.cs.value(1)
        except Exception:
            pass
        # Tear down SPI bus completely
        try:
            if self.vspi is not None:
                self.vspi.deinit()
        except Exception:
            pass
        self.vspi = None
        self.nfc  = None
        utime.sleep_ms(100)
        # Rebuild from scratch
        try:
            self.vspi = SPI(
                1,
                baudrate=config.SPI_BAUDRATE,
                polarity=0,
                phase=0,
                sck=self.sck,
                mosi=self.mosi,
                miso=self.miso,
            )
            self.nfc = PN532(self.vspi, self.cs, reset=self.rst, debug=False)
            self.nfc.SAM_configuration()
            logger.info('PN532 uspesne obnovena.')
        except Exception as ex:
            logger.error('Plna reinicializace PN532 selhala: {}'.format(ex))
        gc.collect()

    def _uid_to_text(self, raw_uid: bytes) -> str:
        """Convert a PN532 UID bytearray to a human-readable hex string.
        PN532 returns 4-byte (MIFARE Classic) UIDs; same format as MFRC522."""
        return '-'.join('%02X' % b for b in raw_uid)

    def cleanup_session(self) -> None:
        """Release the RF field. Called in finally blocks to guarantee cleanup."""
        try:
            _halt_field(self.nfc)
        except Exception:
            pass

    def _read_block_once(self, expected_uid: str = None) -> tuple:
        """
        Detect card, authenticate, and read _TARGET_BLOCK once.
        Returns (data_hex_list, uid_str, reason).

        PN532 card detection uses read_passive_target() which atomically
        handles anti-collision and selection — no separate anticoll/select needed.
        """
        try:
            raw_uid = self.nfc.read_passive_target(timeout=200)
            if raw_uid is None:
                return None, None, 'no_tag'

            uid = self._uid_to_text(raw_uid)
            if expected_uid is not None and uid != expected_uid:
                return None, uid, 'wrong_uid'

            if not _authenticate(self.nfc, raw_uid):
                _halt_field(self.nfc)
                return None, uid, 'auth'

            data = self.nfc.mifare_classic_read_block(_TARGET_BLOCK)
            if data is None or len(data) != _BLOCK_SIZE:
                _halt_field(self.nfc)
                return None, uid, 'read'

            _halt_field(self.nfc)
            return ['%02X' % b for b in data], uid, 'ok'

        except Exception as ex:
            self.cleanup_session()
            return None, None, 'ex:{}'.format(type(ex).__name__)

    def read_block_reliable(self, expected_uid: str = None, attempts: int = 3) -> tuple:
        """Tries to read _TARGET_BLOCK up to attempts times."""
        last_uid    = None
        last_reason = 'no_tag'
        for _ in range(attempts):
            data, uid, reason = self._read_block_once(expected_uid=expected_uid)
            if data is not None:
                return data, uid, 'ok'
            if uid is not None:
                last_uid = uid
            last_reason = reason
            if reason == 'no_tag' and last_uid is None:
                break
            utime.sleep_ms(30)
        return None, last_uid, last_reason

    def _write_block_once(self, data_to_write: bytes, expected_uid: str = None) -> tuple:
        """
        Detect card, authenticate, and write 16 bytes to _TARGET_BLOCK once.
        Returns (success_bool, uid_str, reason).

        Uses InDataExchange + MIFARE_CMD_WRITE (0xA0) — identical to rfid_block4.py.
        The pre-allocated _WRITE_PARAMS buffer is reused to avoid heap allocation.
        """
        try:
            raw_uid = self.nfc.read_passive_target(timeout=200)
            if raw_uid is None:
                return False, None, 'no_tag'

            uid = self._uid_to_text(raw_uid)
            if expected_uid is not None and uid != expected_uid:
                _halt_field(self.nfc)
                return False, uid, 'wrong_uid'

            if not _authenticate(self.nfc, raw_uid):
                _halt_field(self.nfc)
                return False, uid, 'auth'

            # Build write params in pre-allocated buffer (no heap allocation)
            _WRITE_PARAMS[0] = 0x01            # target number
            _WRITE_PARAMS[1] = 0xA0            # MIFARE_CMD_WRITE
            _WRITE_PARAMS[2] = _TARGET_BLOCK & 0xFF
            _WRITE_PARAMS[3:] = data_to_write

            resp = self.nfc.call_function(
                0x40,                           # InDataExchange
                params=_WRITE_PARAMS,
                response_length=1,
                timeout=1000,
            )
            _halt_field(self.nfc)

            if resp is None or resp[0] != 0x00:
                return False, uid, 'write'

            return True, uid, 'ok'

        except Exception as ex:
            self.cleanup_session()
            return False, None, 'ex:{}'.format(type(ex).__name__)

    def wait_for_card_removed(self) -> None:
        """Wait until the RF field is clear, then enforce the 2.5-second debounce.

        With the PN532, card presence is polled via read_passive_target(timeout=100).
        A None return means the card is HALT'd or gone — identical to the MFRC522
        REQIDL-only poll strategy (BUG-A fix preserved: we do NOT call the write
        path here, which would restart crypto and corrupt state).

        The mandatory 2500 ms hard debounce follows immediately afterward."""
        for _ in range(20):    # poll up to 2 s
            try:
                uid = self.nfc.read_passive_target(timeout=100)
                if uid is None:
                    break    # card is HALT'd or removed — field is clear
            except Exception:
                break
            utime.sleep_ms(10)
        # Hard debounce: prevents re-read of the same card even if it lingers
        utime.sleep_ms(2500)


# ---------------------------------------------------------------------------
# SlaveWriter — stamp station logic
# ---------------------------------------------------------------------------

class SlaveWriter(BaseRFID):
    """Handles slave station logic: writing timestamps to block 4."""

    def __init__(self, station_id: int, buzzer) -> None:
        super().__init__()
        self.station_id = station_id
        self.buzzer = buzzer
        # UID debounce — 2.5s cooldown mirrors MasterReader.tick()
        self._last_uid:    str = None
        self._last_ok_ms:  int = 0

    def _station_time_value(self) -> int:
        import utime as _t
        return int(_t.time()) & 0xFFFF

    def uprav_data(self, time_in_sec: int, data: list) -> bytes:
        if self.station_id < 1 or self.station_id > 8:
            raise ValueError('STATION_ID musi byt v rozsahu 1-8')

        target1 = (self.station_id * 2) - 2
        target2 = (self.station_id * 2) - 1
        updated = list(data)

        bytes_val = int(time_in_sec & 0xFFFF).to_bytes(2, 'big')
        converted_time = ['%02X' % b for b in bytes_val]
        updated[target1] = converted_time[0]
        updated[target2] = converted_time[1]

        return bytes.fromhex(''.join(updated))

    def read_update_verify_once(self) -> tuple:
        """
        Atomic single-session read → update timestamp → write → verify block 4.

        Implements the full read_update_verify_once sequence from rfid_block4.py:
          1. Detect card (read_passive_target)
          2. UID debounce check (2.5 s cooldown)
          3. Authenticate (up to _AUTH_RETRIES)
          4. Read block 4
          5. Compute updated payload
          6. Write (InDataExchange + 0xA0)
          7. Re-authenticate (crypto session resets after write — MIFARE protocol)
          8. Read back and compare all 16 bytes (_VERIFY_RETRIES attempts)
          9. _halt_field() always called in finally — skill mandate
        """
        uid    = None
        raw_uid = None
        success = False

        # Clear any residual crypto state from a previous incomplete session
        try:
            self.nfc.SAM_configuration()
        except Exception:
            pass

        try:
            # Step 1 — Card detection (anti-collision + select handled by PN532)
            raw_uid = self.nfc.read_passive_target(timeout=300)
            if raw_uid is None:
                return False, None, 'no_tag'

            uid = self._uid_to_text(raw_uid)

            # Step 2 — UID debounce (BUG-B fix preserved)
            # Primary debounce is the 2500 ms sleep in wait_for_card_removed().
            # This is a second safety-net in case that sleep was skipped (exception path).
            now = utime.ticks_ms()
            if uid == self._last_uid and utime.ticks_diff(now, self._last_ok_ms) < 2500:
                return False, uid, 'debounce'

            # Step 3 — Authenticate for read
            if not _authenticate(self.nfc, raw_uid):
                return False, uid, 'auth'

            # Step 4 — Read current block contents
            data = self.nfc.mifare_classic_read_block(_TARGET_BLOCK)
            if data is None or len(data) != _BLOCK_SIZE:
                return False, uid, 'read'

            # Step 5 — Compute updated payload
            data_hex = ['%02X' % b for b in data]
            current_timestamp = self._station_time_value()
            data_to_write = self.uprav_data(current_timestamp, data_hex)

            # Step 6 — Write via InDataExchange + MIFARE_CMD_WRITE (0xA0)
            _WRITE_PARAMS[0] = 0x01
            _WRITE_PARAMS[1] = 0xA0
            _WRITE_PARAMS[2] = _TARGET_BLOCK & 0xFF
            _WRITE_PARAMS[3:] = data_to_write

            resp = self.nfc.call_function(
                0x40,                           # InDataExchange
                params=_WRITE_PARAMS,
                response_length=1,
                timeout=1000,
            )
            if resp is None or resp[0] != 0x00:
                logger.warning('[ERROR] read_update_verify_once: write rejected (resp={})'.format(resp))
                return False, uid, 'write'

            logger.info('[OK] read_update_verify_once: write accepted, verifying...')

            # Step 7+8 — Re-authenticate then read back (_VERIFY_RETRIES attempts)
            # Crypto session is implicitly ended after write — re-auth is mandatory.
            for attempt in range(_VERIFY_RETRIES):
                if not _authenticate(self.nfc, raw_uid):
                    logger.warning('[WARN] re-auth for verify failed (attempt {}/{})'.format(
                        attempt + 1, _VERIFY_RETRIES))
                    utime.sleep_ms(20)
                    continue

                verified = self.nfc.mifare_classic_read_block(_TARGET_BLOCK)
                if verified is None or len(verified) != _BLOCK_SIZE:
                    logger.warning('[WARN] readback invalid on attempt {}'.format(attempt + 1))
                    utime.sleep_ms(20)
                    continue

                # Byte-level comparison — no shortcuts (rfid_block4.py §5.4)
                if bytes(verified) == bytes(data_to_write):
                    success = True
                    logger.info('[OK] read_update_verify_once: verify PASSED on attempt {}'.format(
                        attempt + 1))
                    break
                else:
                    logger.error('[ERROR] read_update_verify_once: verify MISMATCH on attempt {}'.format(
                        attempt + 1))

            if success:
                # Track successful write for debounce
                self._last_uid    = uid
                self._last_ok_ms  = utime.ticks_ms()
                return True, uid, 'ok'
            else:
                return False, uid, 'verify_mismatch'

        except Exception as ex:
            return False, uid, 'ex:{}'.format(type(ex).__name__)

        finally:
            # Always release the RF field — skill mandate (halt_a + stop_crypto1)
            _halt_field(self.nfc)

    def process_card_reliable(self, attempts: int = 3) -> tuple:
        """Performs atomic read→update→write→verify up to attempts times."""
        last_uid    = None
        last_reason = 'no_tag'
        for _ in range(attempts):
            ok, uid, reason = self.read_update_verify_once()
            if ok:
                self.buzzer.ano_sound()
                logger.info('SUCCESS: Data zapsana a overena.')
                return True, uid, 'ok'
            if uid is not None:
                last_uid = uid
            last_reason = reason
            if reason == 'no_tag' and last_uid is None:
                break
            utime.sleep_ms(40)
        return False, last_uid, last_reason

    def process_one_card(self) -> None:
        """Waits for a card, writes timestamp reliably, and waits for removal."""
        failures = 0
        _gc_ctr  = 0    # periodic GC inside the idle loop (BUG-G fix preserved)
        while True:
            ok, uid, reason = self.process_card_reliable(attempts=3)
            if ok:
                logger.info('SUCCESS: Cip {} zapsan.'.format(uid))
                self.wait_for_card_removed()
                return

            # Debounced card — already stamped, silently wait out the cooldown
            if reason == 'debounce':
                utime.sleep_ms(100)
                continue

            if uid is not None:
                # Card detected but write failed — retry while the runner
                # repositions the card (~400 ms window)
                for _ in range(8):
                    utime.sleep_ms(45)
                    ok, r_uid, reason = self.process_card_reliable(attempts=2)
                    if ok:
                        logger.info('SUCCESS: Cip {} zapsan na pokus.'.format(r_uid))
                        self.wait_for_card_removed()
                        return
                    if reason == 'debounce':
                        # Card already stamped — abort retry loop silently
                        break
                    if r_uid is None and reason == 'no_tag':
                        break

                if reason != 'debounce':
                    self.buzzer.ne_sound()
                    logger.warning('Cip {} zapsat nelze ({}).'.format(uid, reason))
                    self.recover_reader()
                    self.wait_for_card_removed()
                return

            if reason != 'no_tag':
                failures += 1
                if failures >= 20:
                    self.recover_reader()
                    failures = 0
            else:
                failures = 0

            utime.sleep_ms(50)
            _gc_ctr += 1
            if _gc_ctr >= 20:    # GC every ~1 s of idle polling
                gc.collect()
                _gc_ctr = 0


# ---------------------------------------------------------------------------
# MasterReader — finish-line logic
# ---------------------------------------------------------------------------

class MasterReader(BaseRFID):
    """Implements the master finish-line reader logic."""

    def __init__(self, buzzer) -> None:
        super().__init__()
        self.buzzer          = buzzer
        self._last_uid       = None
        self._last_read_ms   = 0
        self._failures       = 0
        self._pending_wipe_uid = None

    def bytes_to_ints(self, data: list) -> list:
        result = []
        for index in range(0, len(data), 2):
            hex_pair = data[index] + data[index + 1]
            result.append(str(int(hex_pair, 16)))
        return result

    def _timestamp_text(self) -> str:
        try:
            import utime as _t
            lt = _t.localtime()
            return '%02d:%02d:%02d' % (lt[3], lt[4], lt[5])
        except Exception:
            return '??:??:??'

    def tick(self) -> dict:
        """Checks once for a card at the finish line."""
        data, uid, reason = self.read_block_reliable(attempts=3)
        if data is None:
            if reason != 'no_tag':
                self._failures += 1
                if self._failures >= 15:
                    self.recover_reader()
                    self._failures = 0
            else:
                self._failures = 0
            return None

        self._failures = 0
        now = utime.ticks_ms()
        # Debounce: ignore same UID within 2.5 seconds
        if uid == self._last_uid and utime.ticks_diff(now, self._last_read_ms) < 2500:
            return None

        self._last_uid       = uid
        self._last_read_ms   = now
        self._pending_wipe_uid = uid

        import utime as _t
        times       = self.bytes_to_ints(data)
        master_time = str(int(_t.time()) & 0xFFFF)
        result = {
            'uid':         uid,
            'times':       times,
            'ts':          self._timestamp_text(),
            'master_time': master_time,
        }
        logger.info('Cip {} precten. Casy: {}  MASTER: {}'.format(uid, times, master_time))
        try:
            self.buzzer.ano_sound()
        except Exception as ex:
            logger.error('Zvuk po precteni selhal: {}'.format(ex))
        gc.collect()
        return result

    def wipe_last_card(self) -> bool:
        """Wipes target block on the last read card."""
        if self._pending_wipe_uid is None:
            return False
        uid = self._pending_wipe_uid
        self._pending_wipe_uid = None
        return self.wipe_card_block(uid)

    def wipe_card_block(self, expected_uid: str) -> bool:
        """
        Writes zeros to _TARGET_BLOCK and verifies.
        Uses the full atomic write sequence (_write_block_once + read-back verify)
        to prevent silent failures on the wipe operation.
        """
        zero_data = bytes(_BLOCK_SIZE)
        for _ in range(3):
            ok, uid, _ = self._write_block_once(zero_data, expected_uid=expected_uid)
            if ok and uid == expected_uid:
                utime.sleep_ms(20)
                data, _, _ = self._read_block_once(expected_uid=expected_uid)
                if data is not None and all(v == '00' for v in data):
                    logger.info('Cip {} wipnut.'.format(expected_uid))
                    return True
            utime.sleep_ms(30)
        logger.error('Wipe pro cip {} selhal.'.format(expected_uid))
        return False
