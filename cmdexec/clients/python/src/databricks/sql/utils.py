from collections import namedtuple, OrderedDict
from enum import Enum

import pyarrow


class ArrowQueue:
    def __init__(self, arrow_table: pyarrow.Table, n_valid_rows: int, start_row_index: int):
        """
        A queue-like wrapper over an Arrow table

        :param arrow_table: The Arrow table from which we want to take rows
        :param n_valid_rows: The index of the last valid row in the table
        :param start_row_index: The first row in the table we should start fetching from
        """
        self.cur_row_index = start_row_index
        self.arrow_table = arrow_table
        self.n_valid_rows = n_valid_rows

    def next_n_rows(self, num_rows: int) -> pyarrow.Table:
        """Get upto the next n rows of the Arrow dataframe"""
        length = min(num_rows, self.n_valid_rows - self.cur_row_index)
        slice = self.arrow_table.slice(self.cur_row_index, length)
        self.cur_row_index += slice.num_rows
        return slice

    def remaining_rows(self) -> pyarrow.Table:
        slice = self.arrow_table.slice(self.cur_row_index, self.n_valid_rows - self.cur_row_index)
        self.cur_row_index += slice.num_rows
        return slice


ExecuteResponse = namedtuple(
    'ExecuteResponse', 'status has_been_closed_server_side has_more_rows description '
    'command_handle arrow_queue arrow_schema')


def _bound(min_x, max_x, x):
    """Bound x by [min_x, max_x]

    min_x or max_x being None means unbounded in that respective side.
    """
    if min_x is None and max_x is None:
        return x
    if min_x is None:
        return min(max_x, x)
    if max_x is None:
        return max(min_x, x)
    return min(max_x, max(min_x, x))


class NoRetryReason(Enum):
    OUT_OF_TIME = "out of time"
    OUT_OF_ATTEMPTS = "out of attempts"
    NOT_RETRYABLE = "non-retryable error"


class RequestErrorInfo(
        namedtuple("RequestErrorInfo_",
                   "error error_message retry_delay http_code method request")):
    @property
    def request_session_id(self):
        if hasattr(self.request, "sessionHandle"):
            return self.request.sessionHandle.sessionId.guid
        else:
            return None

    @property
    def request_query_id(self):
        if hasattr(self.request, "operationHandle"):
            return self.request.operationHandle.operationId.guid
        else:
            return None

    def full_info_logging_str(self, no_retry_reason, attempt, max_attempts, elapsed, max_duration):
        log_base_data_dict = OrderedDict([
            ("Method", self.method),
            ("Session-id", self.request_session_id),
            ("Query-id", self.request_query_id),
            ("HTTP-code", self.http_code),
            ("Error-message", self.error_message),
            ("Original-exception", self.error),
        ])

        if no_retry_reason is not None:
            log_base_data_dict["No-retry-reason"] = no_retry_reason.value
        else:
            log_base_data_dict["Bounded-retry-delay"] = self.retry_delay
            log_base_data_dict["Attempt"] = "{}/{}".format(attempt, max_attempts)
            log_base_data_dict["Elapsed-seconds"] = "{}/{}".format(elapsed, max_duration)

        log_base = "; ".join(["{}: {}".format(k, v) for k, v in log_base_data_dict.items()])

        return log_base

    def user_friendly_error_message(self, no_retry_reason, attempt, elapsed):
        # This should be kept at the level that is appropriate to return to a Redash user
        user_friendly_error_message = "Error during request to server"
        if self.error_message:
            user_friendly_error_message = "{}: {}".format(user_friendly_error_message,
                                                          self.error_message)

        if no_retry_reason is NoRetryReason.OUT_OF_ATTEMPTS:
            user_friendly_error_message = "{}: After {} retry attempts, retries are exhausted".format(
                user_friendly_error_message, attempt)
        elif no_retry_reason is NoRetryReason.OUT_OF_TIME:
            user_friendly_error_message = "{}: After {} seconds, maximum retry duration will be exceeded".format(
                user_friendly_error_message, elapsed)

        return user_friendly_error_message
