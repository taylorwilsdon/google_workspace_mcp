"""Fixed resource ceilings for untrusted or unbounded input.

Every value here is a constant, not an environment variable. The audit findings these
address (1, 3, 11, 12, 19, 20, 32, 44, 50) are all memory-exhaustion DoS: a single
call could ask the process to materialise an arbitrarily large payload. A deployment
that can raise the ceiling can also be talked into raising it, so the limits are
compiled in and the only way to change one is a code change and a release.

Two rules apply wherever these are used:

* **Check before you buffer.** A declared size (``Content-Length``, a file's
  ``st_size``, a base64 string's length) is checked first so an oversized payload is
  rejected without being read.
* **Then count as you read.** Declared sizes are client-supplied or absent, so the
  streamed bytes are counted too and the transfer is abandoned at the limit.
"""

# Whole HTTP request bodies (every MCP tool call arrives as one).
MAX_HTTP_REQUEST_BODY_BYTES = 50 * 1024 * 1024

# Attachments held in the temporary attachment store.
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024

# Gmail's own attachment ceiling, applied to the decoded bytes of every source
# (local path, inline base64, fetched URL) and to their total per message.
MAX_EMAIL_ATTACHMENT_BYTES = 25 * 1024 * 1024

# Google Chat attachment downloads.
MAX_CHAT_ATTACHMENT_BYTES = 50 * 1024 * 1024

# Drive uploads supplied inline as base64: the decoded bytes must fit in memory.
MAX_DRIVE_INLINE_BASE64_BYTES = 32 * 1024 * 1024

# Drive uploads streamed from a URL or a local path. Far larger because nothing is
# buffered -- this bounds disk use and transfer time, not memory.
MAX_DRIVE_STREAMED_BYTES = 2 * 1024 * 1024 * 1024

# Text extracted from a Google Doc / Drive export into memory.
MAX_DOC_CONTENT_BYTES = 50 * 1024 * 1024

# Apps Script project sources. Three separate bounds: a project with many small files
# is as effective a DoS as one enormous file.
MAX_SCRIPT_FILES = 100
MAX_SCRIPT_FILE_BYTES = 5 * 1024 * 1024
MAX_SCRIPT_TOTAL_BYTES = 10 * 1024 * 1024


def max_base64_length_for(max_decoded_bytes: int) -> int:
    """Largest base64 string that could decode to ``max_decoded_bytes``.

    Base64 inflates by 4/3 and pads to a multiple of 4, so anything longer than this
    cannot possibly fit and is rejected without allocating the decoded bytes.
    """
    return ((max_decoded_bytes + 2) // 3) * 4
