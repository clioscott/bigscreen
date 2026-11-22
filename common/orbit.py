"""Sun and Earth-rotation maths shared by the hub (hub/jobs/iss.py: the ISS's track) and the display
(display/modules/issmap.py: day and night on the ISS maps). Low precision (well under 0.1 degree): plenty for a map
and for when the ISS crosses into Earth's shadow."""

import math


def julian(t):
    """Julian date of a Unix time."""
    return t / 86400.0 + 2440587.5


def gmst(jd):
    """Greenwich mean sidereal time (rad)."""
    d = jd - 2451545.0
    return math.radians((280.46061837 + 360.98564736629 * d) % 360.0)


def sun_dir(jd):
    """Unit vector to the Sun in the equatorial frame."""
    n = jd - 2451545.0
    L = math.radians((280.460 + 0.9856474 * n) % 360)
    g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = L + math.radians(1.915) * math.sin(g) + math.radians(0.020) * math.sin(2 * g)
    eps = math.radians(23.439 - 0.0000004 * n)
    return (math.cos(lam), math.cos(eps) * math.sin(lam), math.sin(eps) * math.sin(lam))


def subsolar(t):
    """(lat, lon) in degrees of the point with the Sun straight overhead at Unix time t."""
    jd = julian(t)
    s = sun_dir(jd)
    lat = math.degrees(math.asin(max(-1.0, min(1.0, s[2]))))
    lon = math.degrees(math.atan2(s[1], s[0]) - gmst(jd))
    return lat, (lon + 540.0) % 360.0 - 180.0
