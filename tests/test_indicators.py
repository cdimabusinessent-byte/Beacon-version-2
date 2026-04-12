from app.services.indicators import calculate_rsi


def test_calculate_rsi_returns_high_value_for_strong_uptrend() -> None:
    closes = [44.0, 44.15, 44.35, 44.6, 44.85, 45.05, 45.25, 45.35, 45.6, 45.8, 46.0, 46.15, 46.35, 46.55, 46.75, 46.9]
    rsi = calculate_rsi(closes, period=14)
    assert rsi > 80
