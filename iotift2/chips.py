"""Pin maps for the ESP32 family, used for compile-time pin checks."""


class Chip:
    def __init__(self, name, gpios, input_only=(), flash=(), adc=(), cores=2, pwm_bits=20,
                 ledc_channels=16):
        self.name = name
        self.gpios = set(gpios)
        self.input_only = set(input_only)
        self.flash = set(flash)
        self.adc = set(adc)
        self.cores = cores
        self.pwm_bits = pwm_bits
        self.ledc_channels = ledc_channels


def _r(a, b):
    return list(range(a, b + 1))


CHIPS = {
    'esp32': Chip('esp32',
                  [g for g in _r(0, 39) if g not in (20, 24, 28, 29, 30, 31)],
                  input_only=_r(34, 39), flash=_r(6, 11),
                  adc=[0, 2, 4, 12, 13, 14, 15, 25, 26, 27, 32, 33, 34, 35, 36, 37, 38, 39],
                  cores=2, pwm_bits=20, ledc_channels=16),
    'esp32s2': Chip('esp32s2', _r(0, 21) + _r(26, 46), input_only=[46], flash=_r(26, 32),
                    adc=_r(1, 20), cores=1, pwm_bits=14, ledc_channels=8),
    'esp32s3': Chip('esp32s3', _r(0, 21) + _r(26, 48), flash=_r(26, 32),
                    adc=_r(1, 20), cores=2, pwm_bits=14, ledc_channels=8),
    'esp32c3': Chip('esp32c3', _r(0, 21), flash=_r(12, 17), adc=_r(0, 5), cores=1,
                    pwm_bits=14, ledc_channels=6),
    'esp32c6': Chip('esp32c6', _r(0, 30), flash=_r(24, 30), adc=_r(0, 6), cores=1,
                    pwm_bits=14, ledc_channels=6),
    'esp32h2': Chip('esp32h2', _r(0, 27), flash=_r(15, 21), adc=_r(1, 5), cores=1,
                    pwm_bits=14, ledc_channels=6),
}
