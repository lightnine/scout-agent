import pytest

from scout.cancellation import RunCancellation, RunCancelled


def test_cancellation_can_be_requested_and_cleared():
    token = RunCancellation()
    assert token.is_cancelled() is False
    token.request()
    assert token.is_cancelled() is True
    with pytest.raises(RunCancelled):
        token.ensure_active()
    token.clear()
    token.ensure_active()
