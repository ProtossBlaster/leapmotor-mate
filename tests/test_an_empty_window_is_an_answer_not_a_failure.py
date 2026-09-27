"""A day with no driving must not cost three logins.

Measured on the lab (B10, independent client), asking the energy split for today on a day the
car had not moved:

    WARNING command_client: Energy range fetch (attempt 1): New API rejected request: HTTP 200, code [100, 100]
    WARNING command_client: Energy range fetch (attempt 2): ...
    WARNING command_client: Energy range fetch (attempt 3): ...

Code 100 is the cloud saying "No data found" — the emptiest of answers. `_classify_ec_response`
exists precisely to tell that apart from an auth failure, but the independent client raises for
every non-zero code, so the body never reaches the classifier: the range read falls into the
transport branch, which calls `self._reset()` and tries again. Three resets, and the next call
after each one logs in again — on an account that already spends a login every half hour.
"""
import mate_api  # puts poller/mate_api_runtime on sys.path, as the poller process does
import api_v2_bridge
import command_client as cc


class _Err(Exception):
    pass


def test_the_bridge_keeps_the_cloud_codes_on_its_rejection():
    error = api_v2_bridge._rejection(200, [100, 100])
    assert error.api_codes == (100, 100)
    assert "code" in str(error)


def test_a_no_data_rejection_is_an_empty_window():
    error = _Err("New API rejected request: HTTP 200, code [100, 100]")
    error.api_codes = (100, 100)
    assert cc._classify_client_rejection(error) == "empty"


def test_a_refusal_that_is_not_no_data_is_still_worth_a_retry():
    error = _Err("New API rejected request: HTTP 200, code [39, 39]")
    error.api_codes = (39, 39)
    assert cc._classify_client_rejection(error) == "retry"


def test_an_error_carrying_no_codes_is_treated_as_before():
    """A transport failure has no cloud code: it must keep the retry and the session reset."""
    assert cc._classify_client_rejection(_Err("Connection aborted")) == "retry"
