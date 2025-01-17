from distutils.util import strtobool
from typing import Union, Any, Tuple

from ast import literal_eval

from cachetools import TTLCache, cached

import core.models as models

# maps key to default and type of value
defaults = {
    # basic settings
    "voting_enabled": False,
    "ip_checking": False,
    "new_music_only": False,
    "logging_enabled": True,
    "hashtags_active": True,
    "embed_stream": False,
    "dynamic_embedded_stream": False,
    "online_suggestions": True,
    "number_of_suggestions": 20,
    "people_to_party": 3,
    "alarm_probability": 0.0,
    "buzzer_cooldown": 60.0,
    "downvotes_to_kick": 2,
    "max_download_size": 0.0,
    "max_playlist_items": 10,
    "max_queue_length": 0,
    "additional_keywords": "",
    "forbidden_keywords": "",
    # platforms
    "local_enabled": True,
    "youtube_enabled": True,
    "youtube_suggestions": 2,
    "spotify_enabled": False,
    "spotify_suggestions": 2,
    "spotify_username": "",
    "spotify_password": "",
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "soundcloud_enabled": False,
    "soundcloud_suggestions": 2,
    "soundcloud_auth_token": "",
    "jamendo_enabled": False,
    "jamendo_suggestions": 2,
    "jamendo_client_id": "",
    # sound
    "feed_cava": True,
    "output": "",
    "backup_stream": "",
    # playback
    "paused": False,
    "volume": 1.0,
    "shuffle": False,
    "repeat": False,
    "autoplay": False,
    # lights
    "ups": 30.0,
    "fixed_color": (0, 0, 0),
    "program_speed": 0.5,
    "wled_led_count": 10,
    "wled_ip": "",
    "wled_port": 21324,
    # the concise, but not much shorter version:
    # **{
    #    k: v
    #    for k, v in list(
    #        chain.from_iterable(
    #            [
    #                (f"{device}_brightness", 1.0),
    #                (f"{device}_monochrome", False),
    #                (f"{device}_program", "Disabled"),
    #                (f"last_{device}_program", "Disabled"),
    #            ]
    #            for device in ["ring", "strip", "wled", "screen"]
    #        )
    #    )
    # },
    "ring_brightness": 1.0,
    "ring_monochrome": False,
    "ring_program": "Disabled",
    "last_ring_program": "Disabled",
    "strip_brightness": 1.0,
    "strip_monochrome": False,
    "strip_program": "Disabled",
    "last_strip_program": "Disabled",
    "wled_brightness": 1.0,
    "wled_monochrome": False,
    "wled_program": "Disabled",
    "last_wled_program": "Disabled",
    "screen_brightness": 1.0,
    "screen_monochrome": False,
    "screen_program": "Disabled",
    "last_screen_program": "Disabled",
    "initial_resolution": (0, 0),
    "dynamic_resolution": False,
}

# Settings change very rarely, cache them to reduce database roundtrips.
# This is especially advantageous for suggestions which check whether platforms are enabled.
# There is a data inconsistency issue when a setting is changed in one process.
# Only that process would flush its cache, others would retain the stale value.
# This could be fixed by communicating the cache flush through redis.
# However, with the daphne setup there is currently only one process handling requests,
# and settings are never changed outside a request (especially not in a celery worker).
# So this is fine as long as no additional daphne (or other) workers are used.
# The lights flushes the cache in its update function.
cache = TTLCache(ttl=10, maxsize=128)


@cached(cache)
def get(key: str) -> Union[bool, int, float, str, Tuple]:
    """This method returns the value for the given :param key:.
    Values of non-existing keys are set to their respective default value."""
    # values are stored as string in the database
    # cast the value to its respective type, defined by the default value, before returning it
    default = defaults[key]
    value = models.Setting.objects.get_or_create(
        key=key, defaults={"value": str(default)}
    )[0].value
    if type(default) in (str, int, float):
        return type(default)(value)
    if type(default) == bool:
        # bool("False") does not return False -> special case for bool
        return strtobool(value)
    elif type(default) == tuple:
        # evaluate the stored literal
        return literal_eval(value)


def set(key: str, value: Any) -> None:
    """This method sets the :param value: for the given :param key:."""
    default = defaults[key]
    setting = models.Setting.objects.get_or_create(
        key=key, defaults={"value": str(default)}
    )[0]
    setting.value = value
    setting.save()
    cache.clear()
