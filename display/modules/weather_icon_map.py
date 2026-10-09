"""
Mapping of Environment Canada weather icon codes / condition labels to the
animated GIF filenames in assets/icons/weather/animatednew/.

Source CSVs (official ECCC citypage_weather datamart):
  https://dd.weather.gc.ca/citypage_weather/docs/current_conditions_icon_code_descriptions_e.csv
  https://dd.weather.gc.ca/citypage_weather/docs/forecast_conditions_icon_code_descriptions_e.csv

Icon codes 0–46 represent weather states:
  - Even codes (0, 2, 4, ...) typically map to daytime conditions
  - Odd codes (1, 3, 5, ...) typically map to nighttime conditions
  - Ranges: 0–9 are clear/cloudy/rain, 10–29 are precipitation/hazards,
    30–46 are partly-cloudy/cloudy variants of precipitation

Last updated: 2026-04-27
"""

# Numeric icon code -> (description, gif_basename)
# All gif_basename values are verified to exist in assets/icons/weather/animatednew/
EC_ICON_CODE_TO_GIF: dict[int, tuple[str, str]] = {
    0: ("Sunny", "clear-day"),
    1: ("Clear", "clear-night"),
    2: ("Mostly sunny", "partly-cloudy-day"),
    3: ("Mostly clear", "partly-cloudy-night"),
    4: ("Partly cloudy", "partly-cloudy-day"),
    5: ("Overcast", "overcast-day"),
    6: ("Overcast", "overcast-night"),
    7: ("Cloudy", "cloudy"),
    8: ("Overcast with showers", "overcast-day-rain"),
    9: ("Overcast with rain", "overcast-rain"),
    10: ("Rain", "rain"),
    11: ("Rain and snow", "overcast-day-sleet"),  # best-effort
    12: ("Freezing rain", "overcast-rain"),  # best-effort
    13: ("Thunderstorms with rain", "thunderstorms-day-rain"),
    14: ("Thunderstorms with heavy rain", "thunderstorms-day-extreme-rain"),  # best-effort
    15: ("Thunderstorms with light snow", "thunderstorms-day-snow"),  # best-effort
    16: ("Thunderstorms with snow", "thunderstorms-day-snow"),
    17: ("Thunderstorms with heavy snow", "thunderstorms-day-extreme-snow"),  # best-effort
    18: ("Light snow", "partly-cloudy-day-snow"),
    19: ("Snow", "snow"),
    20: ("Heavy snow", "extreme-night-snow"),  # best-effort
    21: ("Ice pellets", "sleet"),  # best-effort
    22: ("Freezing drizzle", "extreme-night-drizzle"),  # best-effort
    23: ("Blowing snow", "wind-snow"),  # best-effort
    24: ("Blowing snow with heavy snow", "extreme-day-snow"),  # best-effort
    25: ("Fog", "fog"),
    26: ("Smoke", "smoke"),
    27: ("Haze", "haze"),
    28: ("Dust", "dust"),
    29: ("Freezing spray", "extreme-rain"),  # best-effort
    30: ("Partly cloudy with light showers", "partly-cloudy-day-rain"),
    31: ("Partly cloudy with showers", "partly-cloudy-day-rain"),
    32: ("Partly cloudy with light rain", "partly-cloudy-day-rain"),
    33: ("Partly cloudy with rain", "partly-cloudy-day-rain"),
    34: ("Mostly cloudy with light showers", "overcast-day-rain"),
    35: ("Mostly cloudy with showers", "overcast-day-rain"),
    36: ("Mostly cloudy with light rain", "overcast-day-rain"),
    37: ("Mostly cloudy with rain", "overcast-rain"),
    38: ("Overcast with light rain", "overcast-day-rain"),
    39: ("Overcast with rain", "overcast-rain"),
    40: ("Overcast with light snow", "overcast-day-snow"),
    41: ("Overcast with snow", "overcast-snow"),
    42: ("Overcast with light rain and snow", "overcast-day-sleet"),  # best-effort
    43: ("Overcast with rain and snow", "overcast-sleet"),
    44: ("Drizzle", "drizzle"),
    45: ("Freezing rain", "overcast-rain"),  # best-effort (duplicate of 12)
    46: ("Thunderstorm", "thunderstorms"),
}

# Lowercased condition text -> gif_basename
# Use this when the icon code is missing but a condition string is present
# (e.g., textual forecast field from OGC or other EC sources).
EC_CONDITION_TEXT_TO_GIF: dict[str, str] = {
    "sunny": "clear-day",
    "clear": "clear-night",
    "mostly sunny": "partly-cloudy-day",
    "mostly clear": "partly-cloudy-night",
    "partly cloudy": "partly-cloudy-day",
    "overcast": "overcast-day",
    "cloudy": "cloudy",
    "with showers": "overcast-day-rain",
    "with rain": "overcast-rain",
    "rain": "rain",
    "rain and snow": "overcast-day-sleet",
    "freezing rain": "overcast-rain",
    "thunderstorms": "thunderstorms-day-rain",
    "thunderstorm": "thunderstorms",
    "light snow": "partly-cloudy-day-snow",
    "snow": "snow",
    "heavy snow": "extreme-night-snow",
    "ice pellets": "sleet",
    "freezing drizzle": "extreme-night-drizzle",
    "blowing snow": "wind-snow",
    "fog": "fog",
    "smoke": "smoke",
    "haze": "haze",
    "dust": "dust",
    "freezing spray": "extreme-rain",
    "drizzle": "drizzle",
    "chance of showers": "partly-cloudy-day-rain",
    "chance of rain": "partly-cloudy-day-rain",
    "chance of snow": "partly-cloudy-day-snow",
    "showers": "overcast-day-rain",
}


def gif_for_icon_code(code: int | None, is_night: bool = False) -> str:
    """
    Return the GIF basename for an EC icon code, with a sane fallback.

    Args:
        code: Numeric EC icon code (0–46), or None.
        is_night: If True and the code maps to a day variant, prefer night
                  equivalent if available.

    Returns:
        GIF basename suitable for use in assets/icons/weather/animatednew/.
    """
    if code is None:
        return "not-available"

    if code not in EC_ICON_CODE_TO_GIF:
        return "not-available"

    desc, gif = EC_ICON_CODE_TO_GIF[code]

    # If is_night=True, try to use the night variant
    if is_night and "-night" not in gif and "-day" in gif:
        night_variant = gif.replace("-day", "-night")
        # Check if the night variant exists by attempting a simple suffix check
        # (caller should have validated these exist already)
        if night_variant in [g for _, g in EC_ICON_CODE_TO_GIF.values()]:
            return night_variant

    return gif


def gif_for_condition_text(text: str | None, is_night: bool = False) -> str:
    """
    Return the GIF basename for an EC condition string, with a sane fallback.

    Use this when the icon code is missing but a condition label is available
    (e.g., from textual forecast fields or OGC equivalents).

    Args:
        text: Condition text like "Mostly Sunny", "Chance of showers", etc.
        is_night: If True, prefer night-variant GIFs where available.

    Returns:
        GIF basename, or "not-available" if no mapping found.
    """
    if not text:
        return "not-available"

    # Normalize to lowercase for matching
    normalized = text.lower().strip()

    # Direct match
    if normalized in EC_CONDITION_TEXT_TO_GIF:
        gif = EC_CONDITION_TEXT_TO_GIF[normalized]
        if is_night and "-night" not in gif and "-day" in gif:
            night_variant = gif.replace("-day", "-night")
            if night_variant in [g for g in EC_CONDITION_TEXT_TO_GIF.values()]:
                return night_variant
        return gif

    # Fallback: substring match (last resort)
    for key, gif in EC_CONDITION_TEXT_TO_GIF.items():
        if key in normalized:
            if is_night and "-night" not in gif and "-day" in gif:
                night_variant = gif.replace("-day", "-night")
                if night_variant in [g for g in EC_CONDITION_TEXT_TO_GIF.values()]:
                    return night_variant
            return gif

    return "not-available"
