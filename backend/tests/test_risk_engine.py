import pytest

from tradingos.config import (
    ExecutionEnvironment,
    LiveTradingDisabledError,
    assert_environment_allowed,
)
from tradingos.domain.instruments import BTC_USDT_SWAP
from tradingos.risk.engine import RiskConfig, RiskEngine

T = 1_735_689_600_000


def engine(**kw) -> RiskEngine:
    e = RiskEngine(RiskConfig(**kw), BTC_USDT_SWAP)
    e.on_time(T, 10_000)
    return e


def test_size_quarter_percent_rounded_down_to_lot():
    # 10k * 0.25% = 25 USDT risk; short 70,000 / stop 70,700 -> 0.035714 BTC -> floor to 0.0357
    d = engine().authorize_entry(side=-1, entry_price=70_000, stop_price=70_700, equity=10_000, open_positions=0)
    assert d.approved
    assert d.qty == pytest.approx(0.0357)
    assert d.risk_amount <= 25.0
    assert BTC_USDT_SWAP.contracts(d.qty) == pytest.approx(3.57)


def test_leverage_cap_shrinks_tight_stop_position():
    # stop distance 7 USDT would want 3.57 BTC (=25x); cap at 3x equity -> 0.4285 BTC
    d = engine().authorize_entry(side=1, entry_price=70_000, stop_price=69_993, equity=10_000, open_positions=0)
    assert d.approved and d.capped_by_leverage
    assert d.notional <= 30_000 + 1e-6


def test_below_min_size_rejected():
    d = engine().authorize_entry(side=1, entry_price=70_000, stop_price=10_000, equity=100, open_positions=0)
    assert not d.approved and d.reason == "below_min_size"


@pytest.mark.parametrize("side,stop", [(1, 70_100), (-1, 69_900), (1, 70_000)])
def test_stop_on_wrong_side_rejected(side, stop):
    d = engine().authorize_entry(side=side, entry_price=70_000, stop_price=stop, equity=10_000, open_positions=0)
    assert not d.approved and d.reason == "stop_on_wrong_side"


def test_max_open_positions():
    d = engine().authorize_entry(side=1, entry_price=100, stop_price=90, equity=10_000, open_positions=1)
    assert d.reason == "max_open_positions"


def test_daily_loss_limit_resets_next_utc_day():
    e = engine()
    e.record_realized(-100.0)
    assert e.daily_loss_limit_hit
    e.on_time(T + 3_600_000, 9_900)   # same day
    assert e.daily_loss_limit_hit
    e.on_time(T + 86_400_000, 9_900)  # next UTC day
    assert not e.daily_loss_limit_hit


def test_live_environment_is_hard_disabled():
    assert assert_environment_allowed("BACKTEST") is ExecutionEnvironment.BACKTEST
    with pytest.raises(LiveTradingDisabledError):
        assert_environment_allowed(ExecutionEnvironment.LIVE)
    with pytest.raises(LiveTradingDisabledError):
        assert_environment_allowed("LIVE")
