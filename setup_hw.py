import machine
import time
from logger import get_logger

logger = get_logger(__name__)

class Buzzer:
    """Controls the PWM buzzer for playing tones."""
    
    G5: int = 784
    C6: int = 1047
    E6: int = 1319
    G6: int = 1568
    G3: int = 196
    C3: int = 131
    D3: int = 147
    F3: int = 175

    def __init__(self, pin_num: int = 25) -> None:
        self.pin_num = pin_num
        self.pwm = machine.PWM(machine.Pin(self.pin_num))
        self.pwm.duty(0)
        self.bud_zticha()

    def tone(self, frequency: int, duration: int) -> None:
        """Play a specific frequency for a given duration in milliseconds."""
        self.pwm.freq(frequency)
        self.pwm.duty(512)
        time.sleep_ms(duration)
        self.pwm.duty(0)

    def bud_zticha(self) -> None:
        """Ensure the buzzer is silent."""
        self.tone(self.C3, 0)
        time.sleep_ms(1)

    def ano_sound(self) -> None:
        """Play a success sound."""
        self.tone(self.G5, 120)
        time.sleep_ms(40)
        self.tone(self.C6, 120)
        time.sleep_ms(40)
        self.tone(self.E6, 180)
        time.sleep_ms(60)
        self.tone(self.G6, 220)

    def ne_sound(self) -> None:
        """Play a failure/error sound."""
        self.tone(self.G3, 180)
        time.sleep_ms(60)
        self.tone(self.C3, 160)
        time.sleep_ms(60)
        self.tone(self.D3, 140)
        time.sleep_ms(40)
        self.tone(self.F3, 300)

    def sync_confirm(self) -> None:
        """Play a sound to confirm synchronization."""
        self.tone(self.C3, 150)
        time.sleep_ms(20)
        self.tone(self.G3, 150)
        time.sleep_ms(20)
        self.tone(self.C6, 150)
        time.sleep_ms(40)
        self.tone(self.G6, 500)


class HardwareConfig:
    """Reads hardware configuration such as the station ID from DIP switches."""

    @staticmethod
    def get_station_id() -> int:
        """Read the station ID from the DIP switches."""
        bit1 = machine.Pin(13, machine.Pin.IN, machine.Pin.PULL_UP)
        
        # BUG-H fix: GPIO 12 is an ESP32 strapping pin. HIGH at boot => 1.8V flash mode
        # We temporarily set PULL_UP to correctly read the DIP switch (connected to GND),
        # and immediately set it back to PULL_DOWN to be safe for any future soft reboots.
        bit2 = machine.Pin(12, machine.Pin.IN, machine.Pin.PULL_UP)
        
        bit3 = machine.Pin(14, machine.Pin.IN, machine.Pin.PULL_UP)
        bit4 = machine.Pin(27, machine.Pin.IN, machine.Pin.PULL_UP)

        list_of_bits = [bit1, bit2, bit3, bit4]
        binary_list = [str(b.value()) for b in list_of_bits]
        
        # Revert pin 12 to PULL_DOWN to keep device safe for resets
        machine.Pin(12, machine.Pin.IN, machine.Pin.PULL_DOWN)
        
        binary_string = "".join(binary_list)
        decimal_num = int(binary_string, 2)
        
        logger.info(f"Read station ID: {decimal_num}")
        return decimal_num
