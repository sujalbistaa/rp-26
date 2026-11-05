"""Print current dev login codes.

    uv run python scripts/totp.py
"""

import pyotp

from seed import DEV_USERS

for officer_id, _, _, secret in DEV_USERS:
    print(f"{officer_id}  {pyotp.TOTP(secret).now()}")
