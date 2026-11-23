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



def moon_dir(jd):
    """Unit vector to the Moon in the equatorial frame (the main terms only: about a quarter of a degree)."""
    d = jd - 2451545.0
    L = math.radians((218.316 + 13.176396 * d) % 360)          # mean longitude
    M = math.radians((134.963 + 13.064993 * d) % 360)          # mean anomaly
    F = math.radians((93.272 + 13.229350 * d) % 360)           # argument of latitude
    lam = L + math.radians(6.289) * math.sin(M)
    beta = math.radians(5.128) * math.sin(F)
    eps = math.radians(23.439 - 0.0000004 * d)
    x = math.cos(beta) * math.cos(lam)
    y = math.cos(beta) * math.sin(lam)
    z = math.sin(beta)
    return (x, y * math.cos(eps) - z * math.sin(eps), y * math.sin(eps) + z * math.cos(eps))


def sub_point(vec, jd):
    """(lat, lon) of the point on Earth straight under a direction (equatorial unit vector) at jd."""
    lat = math.degrees(math.asin(max(-1.0, min(1.0, vec[2]))))
    lon = math.degrees(math.atan2(vec[1], vec[0]) - gmst(jd))
    return lat, (lon + 540.0) % 360.0 - 180.0


def moon_phase(t):
    """How far through its cycle the Moon is (0 new, 0.5 full) at Unix time t."""
    return ((t / 86400.0 + 2440587.5 - 2451550.1) / 29.530588853) % 1.0
