"""This module contains all programs that use leds."""
import colorsys
import math
from typing import List, Tuple, cast

from core.lights.programs import LedProgram


def stretched_hues(led_count: int, offset: float = 0):
    """Stretches red and blue, compresses green and pink."""
    # Uses the logistic curve to make colors more prominent and compress the others
    #
    #  ^ out hue
    # 1-        xx
    #  |       x
    #  |     xx
    #  |    x
    #  |   x
    #  |   x
    #  |  x
    #  |xx
    #  -|--|--|--|> in hue
    #   R  G  B  R
    #
    # Two logistic curves are combined to compress two colors (green and pink).
    # Green is compressed because the board is green and visible anyway.
    # Pink just does not look that good.

    M1 = 2 / 3
    M2 = 1 / 3

    def f(x):
        # First curve, compresses green (hue = ⅓)
        def L1(x):
            return M1 / (1 + math.e ** (-16 * (x - 1 / 3)))

        # First curve, compresses pink (hue = ⅚)
        def L2(x):
            return M2 / (1 + math.e ** (-16 * (x - 5 / 6)))

        if x < 2 / 3:
            # Vertically stretch and move the curve so it starts at y=0 and ends at y=M
            y0 = L1(0)
            scale = M1 / (M1 - 2 * y0)
            return scale * (L1(x) - y0)
        else:
            y0 = L2(2 / 3)
            scale = M2 / (M2 - 2 * y0)
            return scale * (L2(x) - y0) + M1

    return [f((offset + led / led_count) % 1) % 1 for led in range(0, led_count)]


def stretched_hues_spectrum(led_count: int):
    """Stretches red and blue, compressing green, but removes pink.
    Adds a short red section, because red is chronically underrepresented.
    Doesn't take an offset, because the ends do not match up,
    leading to jumps in hue. Only used for the spectrum."""
    #  ^ out hue
    # 1-
    #  |
    # ⅔-       xx
    #  |      x
    #  |     x
    #  |     x
    #  |    x
    #  |xxxx
    #  -|--|--|--|> in hue
    #   R  G  B  R
    M = 2 / 3

    def f(x):
        def L(x):
            return M / (1 + math.e ** (-12 * (x - 9 / 16)))

        if x < 1 / 8:
            return 0
        y0 = L(1 / 8)
        scale = M / (M - 2 * y0)
        return scale * L(x) - y0

    return [f(led / led_count) % 1 for led in range(0, led_count)]


class Fixed(LedProgram):
    """Show one fixed color only. The color is controlled in the "DeviceManager" class."""

    def __init__(self, manager: "DeviceManager") -> None:
        super().__init__(manager)
        self.manager = manager
        self.name = "Fixed"

    def compute(self) -> None:
        # show a red color if the alarm is active
        alarm_factor = self.manager.alarm_program.factor
        if alarm_factor != -1.0:
            self.manager.fixed_color = (alarm_factor, 0, 0)

    def ring_colors(self) -> List[Tuple[float, float, float]]:
        return [self.manager.fixed_color for _ in range(self.manager.ring.LED_COUNT)]

    def wled_colors(self) -> List[Tuple[float, float, float]]:
        return [self.manager.fixed_color for _ in range(self.manager.wled.led_count)]

    def strip_color(self) -> Tuple[float, float, float]:
        return self.manager.fixed_color


class Rainbow(LedProgram):
    """Continuously cycles through all colors. Affected by the speed setting."""

    def __init__(self, manager: "DeviceManager") -> None:
        super().__init__(manager)
        self.manager = manager
        self.name = "Rainbow"
        self.program_duration = 1
        self.time_passed = 0.0
        self.current_fraction = 0.0

    def start(self) -> None:
        self.time_passed = 0.0

    def compute(self) -> None:
        self.time_passed += self.manager.seconds_per_frame * self.manager.program_speed
        self.time_passed %= self.program_duration
        self.current_fraction = self.time_passed / self.program_duration

    def _colors(self, led_count) -> List[Tuple[float, float, float]]:
        return [
            colorsys.hsv_to_rgb(hue, 1, 1)
            for hue in stretched_hues(led_count, self.current_fraction)
        ]

    def ring_colors(self) -> List[Tuple[float, float, float]]:
        return self._colors(self.manager.ring.LED_COUNT)

    def wled_colors(self) -> List[Tuple[float, float, float]]:
        return self._colors(self.manager.wled.led_count)

    def strip_color(self) -> Tuple[float, float, float]:
        return colorsys.hsv_to_rgb(self.current_fraction, 1, 1)


class Adaptive(LedProgram):
    """Dynamically reacts to the currently played music.
    Low frequencies are represented by red, high ones by blue."""

    def __init__(self, manager: "DeviceManager") -> None:
        super().__init__(manager)
        self.manager = manager
        self.name = "Rave"
        self.cava = self.manager.cava_program

        # RING
        # The spectrum needs to have a color for low frequencies (red)
        # and a color for high frequencies (blue)
        # In order to show a clean separation between the spectrum ends,
        # the color between the two (pink) is removed from the pool of possible colors.
        ring_hues = stretched_hues_spectrum(self.manager.ring.LED_COUNT)
        self.ring_base_colors = [colorsys.hsv_to_rgb(hue, 1, 1) for hue in ring_hues]

        # WLED
        # identical to ring, but with a different number of leds
        wled_hues = stretched_hues_spectrum(self.manager.wled.led_count)
        self.wled_base_colors = [colorsys.hsv_to_rgb(hue, 1, 1) for hue in wled_hues]

        # STRIP
        # distribute frequencies over the three leds. Don't use hard cuts, but smooth functions
        # the functions add up to one at every point and each functions integral is a third
        self.strip_granularity = 16
        self.red_coeffs = [
            -1
            / (
                1
                + math.e ** (-6 * math.e * (led / (self.strip_granularity - 1) - 1 / 3))
            )
            + 1
            for led in range(0, self.strip_granularity)
        ]
        self.blue_coeffs = [
            1
            / (
                1
                + math.e ** (-6 * math.e * (led / (self.strip_granularity - 1) - 2 / 3))
            )
            for led in range(0, self.strip_granularity)
        ]
        self.green_coeffs = [
            1 - self.red_coeffs[led] - self.blue_coeffs[led]
            for led in range(0, self.strip_granularity)
        ]

    def start(self) -> None:
        self.cava.use()

    def compute(self) -> None:
        pass

    def _aggregate_frame(self, led_count) -> List[float]:
        # aggregate the length of cavas frame into a list the length of the number of leds we have.
        # This reduces computation time.
        values_per_led = len(self.cava.current_frame) // led_count
        left = len(self.cava.current_frame) - values_per_led * led_count

        start = 0

        aggregated = []
        for _ in range(led_count):
            end = start + values_per_led
            bin_size = values_per_led
            if left > 0:
                end += 1
                left -= 1
                bin_size += 1

            aggregated.append(sum(self.cava.current_frame[start:end]) / bin_size)
            start += values_per_led
        return aggregated

    def ring_colors(self) -> List[Tuple[float, float, float]]:
        aggregated = self._aggregate_frame(self.manager.ring.LED_COUNT)
        colors = [
            tuple(factor * val for val in color)
            for factor, color in zip(aggregated, self.ring_base_colors)
        ]
        # https://github.com/python/mypy/issues/5068
        return cast(List[Tuple[float, float, float]], colors)

    def wled_colors(self) -> List[Tuple[float, float, float]]:
        aggregated = self._aggregate_frame(self.manager.wled.led_count)
        colors = [
            tuple(factor * val for val in color)
            for factor, color in zip(aggregated, self.wled_base_colors)
        ]
        # https://github.com/python/mypy/issues/5068
        return cast(List[Tuple[float, float, float]], colors)

    def strip_color(self) -> Tuple[float, float, float]:
        aggregated = self._aggregate_frame(self.strip_granularity)
        red = (
            sum(coeff * val for coeff, val in zip(self.red_coeffs, aggregated))
            * 3
            / self.strip_granularity
        )
        green = (
            sum(coeff * val for coeff, val in zip(self.green_coeffs, aggregated))
            * 3
            / self.strip_granularity
        )
        blue = (
            sum(coeff * val for coeff, val in zip(self.blue_coeffs, aggregated))
            * 3
            / self.strip_granularity
        )
        red = min(1.0, red)
        green = min(1.0, green)
        blue = min(1.0, blue)
        return red, green, blue

    def stop(self) -> None:
        self.cava.release()
