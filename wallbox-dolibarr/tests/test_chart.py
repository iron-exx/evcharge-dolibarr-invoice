"""Tagesdiagramm der Verlaufsansicht.

Getrennt geprüft: die Eimer-Bildung (rechnet) und das SVG (stellt dar).
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from web_server import SERIES_BUSINESS, SERIES_PRIVATE, _build_daily_chart, _daily_buckets  # noqa: E402


def _s(day, kwh, status='completed'):
    return {'start_time': f'2026-10-{day:02d}T12:00:00', 'total_kwh': kwh, 'status': status}


# ---- Berechnung -----------------------------------------------------------

def test_empty_month_has_a_bucket_per_day_all_zero():
    buckets = _daily_buckets([], 2026, 10)
    assert len(buckets) == 31, "Oktober hat 31 Tage"
    assert all(b['business'] == 0 and b['private'] == 0 for b in buckets)
    assert buckets[0]['day'] == 1 and buckets[-1]['day'] == 31


def test_february_in_a_leap_year():
    assert len(_daily_buckets([], 2028, 2)) == 29


def test_sessions_are_summed_per_day():
    buckets = _daily_buckets([_s(3, 5.0), _s(3, 2.5), _s(7, 1.0)], 2026, 10)
    by_day = {b['day']: b for b in buckets}
    assert by_day[3]['business'] == pytest.approx(7.5)
    assert by_day[7]['business'] == pytest.approx(1.0)
    assert by_day[4]['business'] == 0


def test_private_sessions_go_into_their_own_series():
    buckets = _daily_buckets([_s(3, 5.0), _s(3, 2.0, status='private')], 2026, 10)
    by_day = {b['day']: b for b in buckets}
    assert by_day[3]['business'] == pytest.approx(5.0)
    assert by_day[3]['private'] == pytest.approx(2.0)
    assert by_day[3]['total'] == pytest.approx(7.0)


def test_discarded_and_incomplete_are_excluded():
    """Verworfen heißt ~0 kWh, unvollständig heißt unbekannt — beides würde
    das Diagramm verfälschen."""
    rows = [_s(3, 0.01, status='discarded'), _s(3, 99.0, status='incomplete'),
            _s(3, 4.0)]
    by_day = {b['day']: b for b in _daily_buckets(rows, 2026, 10)}
    assert by_day[3]['total'] == pytest.approx(4.0)


def test_sessions_from_other_months_are_ignored():
    rows = [{'start_time': '2026-09-15T12:00:00', 'total_kwh': 9.0, 'status': 'completed'},
            _s(5, 1.0)]
    assert sum(b['total'] for b in _daily_buckets(rows, 2026, 10)) == pytest.approx(1.0)


def test_broken_timestamps_do_not_crash():
    rows = [{'start_time': None, 'total_kwh': 1.0, 'status': 'completed'},
            {'start_time': 'kaputt', 'total_kwh': 1.0, 'status': 'completed'},
            {'start_time': '2026-10-05T12:00:00', 'total_kwh': None, 'status': 'completed'}]
    buckets = _daily_buckets(rows, 2026, 10)
    assert sum(b['total'] for b in buckets) == 0


# ---- Darstellung ----------------------------------------------------------

def test_empty_chart_says_so_instead_of_drawing_nothing():
    html = _build_daily_chart([], 2026, 10)
    assert 'Keine' in html or 'keine' in html
    assert '<svg' not in html, "ein leeres Achsenkreuz hilft niemandem"


def test_chart_draws_one_bar_group_per_day_with_data():
    html = _build_daily_chart([_s(3, 5.0), _s(7, 2.0)], 2026, 10)
    assert html.count('class="bar"') == 2, "nur Tage mit Werten bekommen einen Balken"
    assert '<svg' in html


def test_both_series_appear_with_a_legend():
    """Ab zwei Serien ist eine Legende Pflicht — Identität darf nie nur an
    der Farbe hängen."""
    html = _build_daily_chart([_s(3, 5.0), _s(3, 2.0, status='private')], 2026, 10)
    assert SERIES_BUSINESS in html and SERIES_PRIVATE in html
    assert 'Geschäftlich' in html and 'Privat' in html
    assert 'legend' in html


def test_single_series_needs_no_legend():
    html = _build_daily_chart([_s(3, 5.0)], 2026, 10)
    assert 'Privat' not in html, "ohne private Ladung keine überflüssige Legende"


def test_largest_day_is_labelled_directly():
    """Direkte Beschriftung nur selektiv — nicht auf jedem Balken."""
    html = _build_daily_chart([_s(3, 5.0), _s(7, 12.5), _s(9, 1.0)], 2026, 10)
    assert '12.5' in html.replace(',', '.')
    assert html.count('class="bar-label"') == 1


def test_every_bar_has_a_hover_tooltip():
    html = _build_daily_chart([_s(3, 5.0), _s(7, 2.0)], 2026, 10)
    assert html.count('<title>') == 2, "jeder Balken braucht einen Tooltip"


def test_values_are_escaped():
    html = _build_daily_chart([_s(3, 5.0)], 2026, 10)
    assert '<script' not in html


def test_stacked_segments_keep_a_two_pixel_surface_gap():
    """Gestapelte Segmente dürfen sich nicht berühren — ohne Spalt verschmelzen
    zwei Farben optisch zu einem Balken."""
    import re
    html = _build_daily_chart([_s(3, 8.0), _s(3, 4.0, status='private')], 2026, 10)
    rects = [tuple(map(float, m)) for m in re.findall(
        r'<rect x="([\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)"', html)]
    assert len(rects) == 2, "geschäftlich und privat am selben Tag = zwei Segmente"
    lower, upper = sorted(rects, key=lambda r: -r[1])
    gap = lower[1] - (upper[1] + upper[3])
    assert gap == pytest.approx(2.0, abs=0.2), f"Spalt ist {gap:.2f}px, erwartet 2px"


# ---- 14-Tage-Streifen auf der Hauptseite ---------------------------------

def test_trend_strip_covers_the_requested_window():
    from web_server import _build_trend_strip
    from datetime import datetime, timedelta
    today = datetime(2026, 10, 20)
    rows = [{'start_time': (today - timedelta(days=d)).strftime('%Y-%m-%dT10:00:00'),
             'total_kwh': 4.0, 'status': 'completed'} for d in (0, 3, 13, 30)]
    html = _build_trend_strip(rows, days=14, today=today)
    assert html.count('class="spark-bar"') == 3, "nur Tage im Fenster mit Werten"
    assert '<svg' in html


def test_trend_strip_is_empty_without_data():
    from web_server import _build_trend_strip
    from datetime import datetime
    html = _build_trend_strip([], days=14, today=datetime(2026, 10, 20))
    assert '<svg' not in html
    assert 'noch keine' in html.lower()


def test_axis_ceiling_hugs_the_data():
    """Ein zu großzügiger Achsen-Höchstwert verschenkt Plothöhe und lässt die
    Balken flach aussehen."""
    from web_server import _nice_ceiling
    for value, expected in [(59.1, 60), (42.0, 50), (9.3, 10), (13.0, 15),
                            (120.0, 150), (0.4, 0.4), (0.41, 0.5), (1.0, 1.0)]:
        got = _nice_ceiling(value)
        assert got == pytest.approx(expected), f"_nice_ceiling({value}) = {got}, erwartet {expected}"
        assert got >= value


def test_tallest_bar_uses_most_of_the_plot_height():
    rows = [_s(5, 59.1), _s(9, 20.0)]
    html = _build_daily_chart(rows, 2026, 10)
    import re
    rects = [tuple(map(float, m)) for m in re.findall(
        r'<rect x="([\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)"', html)]
    tallest = max(h for _, _, _, h in rects)
    plot_h = 190.0 - 16.0 - 26.0
    assert tallest / plot_h > 0.9, f"höchster Balken nutzt nur {tallest / plot_h:.0%} der Plothöhe"
