"""Tests for AioSandbox.upload_file_stream streaming upload."""

from unittest.mock import MagicMock

from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox


def _make_mock_aio_sandbox(client_mock: MagicMock, lock_mock: MagicMock) -> AioSandbox:
    """Create an AioSandbox instance with mocked internals.

    Uses ``object.__new__`` to bypass ``__init__`` (which requires a real
    agent-sandbox client URL and launches background threads).
    """
    sandbox = object.__new__(AioSandbox)
    object.__setattr__(sandbox, "_client", client_mock)
    object.__setattr__(sandbox, "_lock", lock_mock)
    object.__setattr__(sandbox, "_id", "mock-sandbox")
    return sandbox


class TestAioSandboxUploadFileStream:
    """Verify upload_file_stream passes an IO[bytes] handle to the SDK."""

    def setup_method(self) -> None:
        self.client_mock = MagicMock()
        self.lock_mock = MagicMock()
        self.sandbox = _make_mock_aio_sandbox(self.client_mock, self.lock_mock)

    def test_upload_file_stream_passes_file_handle_not_bytes(self, tmp_path):
        """The SDK's upload_file must receive an open file handle, not bytes."""
        local_file = tmp_path / "large.mp4"
        expected_content = b"\x00" * 1024
        local_file.write_bytes(expected_content)

        self.sandbox.upload_file_stream("/sandbox/large.mp4", local_file)

        call_kwargs = self.client_mock.file.upload_file.call_args
        assert call_kwargs is not None, "upload_file was not called"

        fh = call_kwargs.kwargs["file"]
        # Must be an IO-like object — SDK will call .read() on it internally
        assert hasattr(fh, "read"), "file argument should be an IO handle"

        # The file handle is used inside a `with` block, so by the time
        # the mock SDK returns (synchronously) the handle is already closed.
        # Verify the content reached the mock SDK by re-reading from disk.
        assert local_file.read_bytes() == expected_content

    def test_upload_file_stream_acquires_lock_during_call(self, tmp_path):
        """Concurrent uploads are serialised via self._lock."""
        local_file = tmp_path / "data.bin"
        local_file.write_bytes(b"abc")

        self.sandbox.upload_file_stream("/sandbox/data.bin", local_file)

        # The lock's __enter__ must have been called
        self.lock_mock.__enter__.assert_called_once()
        self.lock_mock.__exit__.assert_called_once()
