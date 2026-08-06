"""Historical market sequences: Shiller-derived annual REAL returns + the
survival engine that replays them.

The table below is derived from Robert Shiller's public U.S. market dataset
(ie_data.xls as published at shillerdata.com; vintage saved 2026-08-04, data
through Aug 2026 — the sheet notes recent CPI values are estimated). Each row
is one calendar year's REAL (inflation-adjusted) total return, January to
January, dividends/coupons reinvested:

  stock = Real Total Return Price index (Shiller col "Real Total Return
          Price"), Jan(y+1)/Jan(y) - 1
  bond  = Real Total Bond Returns index (Shiller col "Real Total Bond
          Returns", a 10-year Treasury constant-maturity total-return
          series), Jan(y+1)/Jan(y) - 1

Derivation checks (recomputed when the table was generated): real stock CAGR
1871-2025 = 7.09%, real bond CAGR = 2.42%, and a 4%-rule replay reproduces
the literature (30-year 75/25: 123 of 126 starts survive).

The engine is deliberately pot-normalized: a retirement that starts with pot
P and spends S per year behaves identically to pot 1.0 spending S/P — so
every survival figure depends only on the withdrawal rate and the mix, and
the caller scales dollars back in. All arithmetic is plain floats on a
normalized pot; no money formatting happens here.
"""
from typing import NamedTuple

# (year, real stock return, real bond return) — see module docstring for
# provenance. 155 rows, 1871-2025.
SHILLER_REAL_RETURNS: tuple[tuple[int, float, float], ...] = (
    (1871, +0.13905, +0.03567), (1872, +0.08781, +0.01556), (1873, +0.02034, +0.11476),
    (1874, +0.12501, +0.16787), (1875, +0.11824, +0.15672), (1876, -0.14916, +0.04872),
    (1877, +0.16973, +0.24974), (1878, +0.29615, +0.17494), (1879, +0.23799, -0.12247),
    (1880, +0.34365, +0.13173), (1881, -0.07193, -0.03392), (1882, +0.05587, +0.05572),
    (1883, +0.02311, +0.12332), (1884, -0.02306, +0.16509), (1885, +0.34535, +0.08557),
    (1886, +0.11940, +0.02203), (1887, -0.05146, -0.02289), (1888, +0.08209, +0.10568),
    (1889, +0.12418, +0.08939), (1890, -0.08447, -0.00628), (1891, +0.26603, +0.10587),
    (1892, -0.01519, -0.04955), (1893, -0.06391, +0.20143), (1894, +0.08058, +0.10335),
    (1895, +0.03459, +0.00918), (1896, +0.06255, +0.08405), (1897, +0.16934, +0.00897),
    (1898, +0.27520, +0.04004), (1899, -0.11309, -0.12122), (1900, +0.23938, +0.06170),
    (1901, +0.16586, +0.00014), (1902, -0.01232, -0.06747), (1903, -0.13275, +0.07246),
    (1904, +0.29134, +0.00491), (1905, +0.21305, +0.03946), (1906, -0.03632, -0.02820),
    (1907, -0.22517, +0.04374), (1908, +0.34974, +0.01485), (1909, +0.05016, -0.07244),
    (1910, +0.03594, +0.10885), (1911, +0.04599, +0.04894), (1912, -0.00104, -0.06183),
    (1913, -0.06643, +0.04725), (1914, -0.06406, +0.02581), (1915, +0.27434, +0.02794),
    (1916, -0.03792, -0.08708), (1917, -0.31921, -0.15031), (1918, +0.00176, -0.10725),
    (1919, +0.02273, -0.13645), (1920, -0.12619, +0.05812), (1921, +0.23761, +0.25425),
    (1922, +0.29897, +0.04532), (1923, +0.02409, +0.03771), (1924, +0.27116, +0.05753),
    (1925, +0.21654, +0.01862), (1926, +0.14115, +0.08995), (1927, +0.38745, +0.04670),
    (1928, +0.49348, +0.02382), (1929, -0.09427, +0.06232), (1930, -0.16895, +0.10698),
    (1931, -0.38028, +0.11918), (1932, +0.04020, +0.18406), (1933, +0.53104, +0.02559),
    (1934, -0.10711, +0.02845), (1935, +0.52711, +0.02506), (1936, +0.29893, +0.00250),
    (1937, -0.32561, +0.03004), (1938, +0.18949, +0.05800), (1939, +0.03798, +0.04428),
    (1940, -0.10171, +0.03028), (1941, -0.18337, -0.12280), (1942, +0.12971, -0.04868),
    (1943, +0.20069, -0.00530), (1944, +0.17002, +0.01127), (1945, +0.36300, +0.01670),
    (1946, -0.25535, -0.13912), (1947, -0.06893, -0.08684), (1948, +0.08199, +0.02291),
    (1949, +0.20112, +0.04424), (1950, +0.24514, -0.07262), (1951, +0.16826, -0.02547),
    (1952, +0.14258, +0.01077), (1953, +0.01877, +0.04843), (1954, +0.47925, +0.02021),
    (1955, +0.28459, -0.00076), (1956, +0.03779, -0.04439), (1957, -0.09095, +0.03187),
    (1958, +0.38440, -0.05695), (1959, +0.06545, -0.02306), (1960, +0.04758, +0.09921),
    (1961, +0.18303, +0.01244), (1962, -0.03862, +0.04760), (1963, +0.19271, -0.00409),
    (1964, +0.14886, +0.03097), (1965, +0.09517, -0.00990), (1966, -0.09532, +0.01717),
    (1967, +0.12036, -0.05750), (1968, +0.05968, -0.02500), (1969, -0.13869, -0.11199),
    (1970, +0.02136, +0.13923), (1971, +0.10389, +0.05104), (1972, +0.13721, -0.01159),
    (1973, -0.23459, -0.05826), (1974, -0.29391, -0.06984), (1975, +0.30437, -0.00263),
    (1976, +0.05732, +0.06327), (1977, -0.14817, -0.04325), (1978, +0.06401, -0.07758),
    (1979, +0.02863, -0.13349), (1980, +0.12734, -0.09971), (1981, -0.14377, -0.05201),
    (1982, +0.25477, +0.38073), (1983, +0.15543, -0.00311), (1984, +0.04258, +0.11072),
    (1985, +0.21678, +0.22304), (1986, +0.29522, +0.22492), (1987, -0.06174, -0.06422),
    (1988, +0.12700, +0.01461), (1989, +0.16928, +0.09568), (1990, -0.06133, +0.03861),
    (1991, +0.28612, +0.13378), (1992, +0.04343, +0.07039), (1993, +0.08959, +0.10041),
    (1994, -0.01597, -0.09837), (1995, +0.31736, +0.21048), (1996, +0.23617, -0.03465),
    (1997, +0.25938, +0.13235), (1998, +0.29354, +0.10367), (1999, +0.12498, -0.11106),
    (2000, -0.08624, +0.14394), (2001, -0.14451, +0.04843), (2002, -0.22148, +0.10316),
    (2003, +0.26154, +0.01152), (2004, +0.03001, +0.00690), (2005, +0.05920, -0.01268),
    (2006, +0.11089, -0.00001), (2007, -0.05470, +0.08941), (2008, -0.35650, +0.14737),
    (2009, +0.29848, -0.09257), (2010, +0.14519, +0.04394), (2011, +0.00469, +0.12850),
    (2012, +0.14401, +0.00716), (2013, +0.23656, -0.07372), (2014, +0.13580, +0.11872),
    (2015, -0.04750, -0.01148), (2016, +0.18159, -0.03660), (2017, +0.22459, -0.01054),
    (2018, -0.06203, +0.00198), (2019, +0.25041, +0.08340), (2020, +0.16238, +0.05828),
    (2021, +0.13709, -0.11418), (2022, -0.17310, -0.17203), (2023, +0.19831, -0.03501),
    (2024, +0.22154, -0.03354), (2025, +0.14596, +0.05403),
)

RETIREMENT_YEARS = 40          # every replay runs a 40-year retirement
AHEAD_MULT = 2.0               # "2x+ ahead" band: pot doubled, in real terms
GUARD_TRIGGERS = (0.85, 0.75, 0.65, 0.55)   # pot fractions tried as the rescue trigger
GUARD_MAX_CUT = 0.60           # past a 60% trim it is a different life, not a guardrail


class Guardrail(NamedTuple):
    trigger_frac: float        # spend drops while the pot sits below this x start
    cut_frac: float            # ... by this fraction (0.07 = trim 7%)


class SurvivalTables(NamedTuple):
    """Everything the page needs, per withdrawal rate (indexes align with the
    `rates` argument). Counts, never percentages — honesty is the format."""
    n_seq: int                 # how many full 40-year start sequences exist
    start_lo: int              # first start year (1871)
    start_hi: int              # last start year with a complete window
    data_hi: int               # last data year in the table
    survived: list[int]        # per rate: sequences that never hit zero
    bands: list[list[list[int]]]   # per rate: [year 1..40][depleted, holding, ahead]
    unspent_frac: list[float | None]  # per rate: median ending pot multiple, survivors only
    guard: list[Guardrail | None]     # per rate: minimal rescue found, None if none needed


def _blend(stock_pct: float) -> list[float]:
    """Per-year real return of a fixed stock/bond mix, rebalanced annually."""
    w = stock_pct / 100.0
    return [w * s + (1.0 - w) * b for _, s, b in SHILLER_REAL_RETURNS]


def _replay(returns: list[float], start: int, w: float,
            guard: Guardrail | None = None) -> tuple[bool, float, list[float]]:
    """One sequence: pot 1.0, withdraw at the top of each year (w = rate/100,
    real dollars, so spending keeps its purchasing power), then apply that
    year's real return. Returns (survived, ending balance, balance after each
    year). A depleted pot stays at 0 — no resurrection."""
    bal, path = 1.0, []
    for k in range(RETIREMENT_YEARS):
        wd = w
        if guard and bal < guard.trigger_frac:
            wd = w * (1.0 - guard.cut_frac)
        bal = (bal - wd) * (1.0 + returns[start + k])
        if bal <= 0.0:
            path.extend([0.0] * (RETIREMENT_YEARS - k))
            return False, 0.0, path
        path.append(bal)
    return True, bal, path


def _rescues_all(returns: list[float], starts: range, w: float,
                 guard: Guardrail) -> bool:
    return all(_replay(returns, s, w, guard)[0] for s in starts)


def _minimal_guardrail(returns: list[float], starts: range,
                       w: float) -> Guardrail | None:
    """The smallest spend trim (whole percents, mildest trigger first) whose
    while-below-the-line rule rescues every failing historical sequence."""
    best: Guardrail | None = None
    for trig in GUARD_TRIGGERS:
        lo, hi = 1, int(GUARD_MAX_CUT * 100)          # cut, in whole percents
        if not _rescues_all(returns, starts, w, Guardrail(trig, hi / 100.0)):
            continue
        while lo < hi:                                 # minimal rescuing cut
            mid = (lo + hi) // 2
            if _rescues_all(returns, starts, w, Guardrail(trig, mid / 100.0)):
                hi = mid
            else:
                lo = mid + 1
        if best is None or hi < int(best.cut_frac * 100):
            best = Guardrail(trig, hi / 100.0)
    return best


def survival_tables(rates: list[float], stock_pct: float) -> SurvivalTables:
    """Replay every complete 40-year start sequence at each withdrawal rate.
    `rates` are annual withdrawal percentages (4.0 = spend 4% of the pot in
    year one, then the same real dollars every year after)."""
    returns = _blend(stock_pct)
    n_starts = len(returns) - RETIREMENT_YEARS + 1
    starts = range(n_starts)
    survived, bands, unspent, guards = [], [], [], []
    for rate in rates:
        w = rate / 100.0
        n_ok, endings = 0, []
        year_bands = [[0, 0, 0] for _ in range(RETIREMENT_YEARS)]
        any_fail = False
        for s in starts:
            ok, end, path = _replay(returns, s, w)
            n_ok += ok
            any_fail |= not ok
            if ok:
                endings.append(end)
            for k, bal in enumerate(path):
                if bal <= 0.0:
                    year_bands[k][0] += 1
                elif bal >= AHEAD_MULT:
                    year_bands[k][2] += 1
                else:
                    year_bands[k][1] += 1
        survived.append(n_ok)
        bands.append(year_bands)
        endings.sort()
        n = len(endings)
        unspent.append(None if n == 0 else
                       (endings[n // 2] if n % 2 else
                        (endings[n // 2 - 1] + endings[n // 2]) / 2.0))
        guards.append(_minimal_guardrail(returns, starts, w)
                      if any_fail else None)
    first_year = SHILLER_REAL_RETURNS[0][0]
    last_year = SHILLER_REAL_RETURNS[-1][0]
    return SurvivalTables(
        n_seq=n_starts, start_lo=first_year,
        start_hi=first_year + n_starts - 1, data_hi=last_year,
        survived=survived, bands=bands, unspent_frac=unspent, guard=guards)
