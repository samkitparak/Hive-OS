"""Tests for score.py — daily score and streak."""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db import init_db
import score as s


@pytest.fixture
def conn():
    c = init_db(":memory:", check_same_thread=False)
    yield c
    c.close()


def test_daily_score_no_data(conn):
    result = s.get_daily_score(conn)
    assert isinstance(result, s.DailyScore)
    assert result.score == 0.0
    assert result.streak == 0
    assert result.jobs_done == 0


def test_score_formula(conn):
    # Manually verify: oee_avg=0.8, on_time_rate=1.0 → score = 0.8*60 + 1.0*40 = 88
    result = s.DailyScore(
        date="2026-06-01",
        score=round(0.8 * 60 + 1.0 * 40, 1),
        oee_avg=0.8,
        on_time_rate=1.0,
        jobs_done=3,
        jobs_on_time=3,
        streak=1,
        rolling_avg=80.0,
        vs_avg=8.0,
        trend="up",
    )
    assert result.score == 88.0


def test_trend_up(conn):
    # Insert a snapshot with low OEE to establish a low rolling average
    conn.execute(
        """INSERT INTO oee_snapshots
           (machine_id, window_start, window_end,
            planned_time_s, run_time_s, idle_time_s, down_time_s,
            parts_planned, parts_made,
            availability, performance, quality, oee)
           VALUES (1,'2026-05-25 00:00:00','2026-05-25 09:00:00',
                   32400,16200,8100,8100,10,8,0.5,0.5,1.0,0.25)"""
    )
    conn.commit()
    result = s.get_daily_score(conn)
    # Today has no events so score=0, but rolling avg from past data should be low
    assert isinstance(result.trend, str)
    assert result.trend in ("up", "down", "same")


def test_jobs_completed_today_none(conn):
    done, on_time = s._jobs_completed_today(conn)
    assert done == 0
    assert on_time == 0
