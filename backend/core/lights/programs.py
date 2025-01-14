"""This module contains all programs used for visualization (except complex screen programs)."""

from __future__ import annotations

import errno
import logging
import os
import subprocess
from typing import Tuple, List, Optional, TYPE_CHECKING

from django.conf import settings as conf

from core.lights import leds

enabled = True

if TYPE_CHECKING:
    from core.lights.worker import DeviceManager


class LightProgram:
    """The base class for all programs."""

    def __init__(self, manager: "DeviceManager") -> None:
        self.manager = manager
        self.consumers = 0
        self.name = "Unknown"

    def start(self) -> None:
        """Initializes the program, allocates resources."""

    def use(self) -> None:
        """Tells the program that it is used by another consumer.
        Starts the program if this is the first usage."""
        if self.consumers == 0:
            self.start()
        self.consumers += 1

    def compute(self) -> None:
        """Is called once per update. Any computation should happen here,
        so it can be reused in the returning functions"""

    def stop(self) -> None:
        """Stops the program, releases resources."""

    def release(self) -> None:
        """Tells the program that one consumer does not use it anymore.
        Stops the program if this was the last one."""
        self.consumers -= 1
        if self.consumers == 0:
            self.stop()


class LedProgram(LightProgram):
    """The base class for all led visualization programs."""

    def ring_colors(self) -> List[Tuple[float, float, float]]:
        """Returns the colors for the ring, one rgb tuple for each led."""
        raise NotImplementedError()

    def wled_colors(self) -> List[Tuple[float, float, float]]:
        """Returns the colors for WLED, one rgb tuple for each led."""
        raise NotImplementedError()

    def strip_color(self) -> Tuple[float, float, float]:
        """Returns the rgb values for the strip."""
        raise NotImplementedError()


class ScreenProgram(LightProgram):
    """Base calls for all programs showing something on screen."""


class Disabled(LedProgram, LightProgram):
    """A null class to represent inactivity."""

    def __init__(self, manager: "DeviceManager") -> None:
        super().__init__(manager)
        self.name = "Disabled"

    def draw(self) -> None:
        raise NotImplementedError()

    def ring_colors(self) -> List[Tuple[float, float, float]]:
        raise NotImplementedError()

    def wled_colors(self) -> List[Tuple[float, float, float]]:
        raise NotImplementedError()

    def strip_color(self) -> Tuple[float, float, float]:
        raise NotImplementedError()


class Alarm(LightProgram):
    """This program makes the leds flash red in sync to the played sound.
    Only computes the brightness, does not display it."""

    def __init__(self, manager: "DeviceManager") -> None:
        super().__init__(manager)
        self.manager = manager
        self.name = "Alarm"
        self.time_passed = 0.0
        self.sound_count = 0
        self.increasing_duration = 0.45
        self.decreasing_duration = 0.8
        # only during this program, thus False by default
        self.pwr_led_enabled = False
        self.sound_duration = 2.1
        self.sound_repetition = 2.5
        self.factor = -1.0

    def start(self) -> None:
        self.time_passed = 0.0
        self.sound_count = 0
        self.factor = 0

    def compute(self) -> None:
        """If active, compute the brightness for the red color,
        depending on the time that has passed since starting the sound."""
        # do not compute if the alarm is not active
        if self.consumers == 0:
            return
        self.time_passed += self.manager.seconds_per_frame
        if self.time_passed >= self.sound_repetition:
            self.sound_count += 1
            self.time_passed %= self.sound_repetition

        if self.sound_count >= 4:
            self.factor = 0
            return
        if self.time_passed < self.increasing_duration:
            self.factor = self.time_passed / self.increasing_duration
        elif self.time_passed < self.sound_duration - self.decreasing_duration:
            self.factor = 1
        elif self.time_passed < self.sound_duration:
            self.factor = (
                1
                - (self.time_passed - (self.sound_duration - self.decreasing_duration))
                / self.decreasing_duration
            )
        else:
            self.factor = 0

        # Ideally, the pwr led would flash with increasing frequency,
        # but the Pi can not handle enough script executions to make it look good.
        if self.pwr_led_enabled and self.factor < 0.7:
            leds.disable_pwr_led()
            self.pwr_led_enabled = False
        elif not self.pwr_led_enabled and self.factor >= 0.7:
            leds.enable_pwr_led()
            self.pwr_led_enabled = True

    def stop(self) -> None:
        self.factor = -1.0


class Cava(LightProgram):
    """This Program manages the interaction with cava.
    It provides the current frequencies for other programs to use."""

    def __init__(self, manager: "DeviceManager") -> None:
        super().__init__(manager)
        self.manager = manager

        self.cava_fifo_path = os.path.join(conf.BASE_DIR, "config/cava_fifo")

        # Keep these configurations in sync with config/cava.config
        self.bars = 256
        self.bit_format = 8

        self.frame_length = self.bars * (self.bit_format // 8)

        self.current_frame: List[float] = []
        self.growing_frame = b""
        self.cava_process: Optional[subprocess.Popen[bytes]] = None
        self.cava_fifo = -1

    def start(self) -> None:
        self.current_frame = [0 for _ in range(self.bars)]
        self.growing_frame = b""
        try:
            # delete old contents of the pipe
            os.remove(self.cava_fifo_path)
        except FileNotFoundError:
            # the file does not exist
            pass
        try:
            old_umask = os.umask(0o002)
            os.mkfifo(self.cava_fifo_path)
        except FileExistsError:
            # the file already exists
            logging.info("%s already exists while starting", self.cava_fifo_path)
        finally:
            os.umask(old_umask)

        self.manager.set_cava_framerate()
        self.cava_process = subprocess.Popen(
            ["cava", "-p", os.path.join(conf.BASE_DIR, "config/cava.config")],
            cwd=conf.BASE_DIR,
            env={"PULSE_SERVER": conf.PULSE_SERVER, **os.environ},
        )
        # cava_fifo = open(cava_fifo_path, 'r')
        self.cava_fifo = os.open(self.cava_fifo_path, os.O_RDONLY | os.O_NONBLOCK)

    def compute(self) -> None:
        """If active, read output from the cava program.
        Make sure that the most recent frame is always fully available,
        Stores incomplete frames for the next update."""
        # do not compute if no program uses cava
        if self.consumers == 0:
            return
        # read the fifo until we get to the current frame
        while True:
            try:
                read = os.read(
                    self.cava_fifo, self.frame_length - len(self.growing_frame)
                )
                if read == b"":
                    return
                self.growing_frame += read
            except OSError as e:
                if e.errno == errno.EAGAIN or e.errno == errno.EWOULDBLOCK:
                    # there were not enough bytes for a whole frame, keep the old frame
                    return

            # we read a whole frame, update the factors
            if len(self.growing_frame) == self.frame_length:
                self.current_frame = [int(b) / 255 for b in self.growing_frame]
                self.growing_frame = b""

    def stop(self) -> None:
        try:
            os.close(self.cava_fifo)
        except OSError as e:
            logging.info("fifo already closed: %s", e)
        except TypeError as e:
            logging.info("fifo does not exist: %s", e)

        if self.cava_process:
            self.cava_process.terminate()

        try:
            os.remove(self.cava_fifo_path)
        except FileNotFoundError as e:
            # the file was already deleted
            logging.info("%s not found while deleting: %s", self.cava_fifo_path, e)
