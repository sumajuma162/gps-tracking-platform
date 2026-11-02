"""GT06 packets from the published GT06 protocol document (login, login reply, location 0x12).

CRC values are the document's own; the decoder recomputes them, so these prove the CRC-ITU implementation.
"""
LOGIN = bytes.fromhex("78780D01012345678901234500018CDD0D0A")
LOGIN_REPLY = bytes.fromhex("787805010001D9DC0D0A")
LOCATION = bytes.fromhex("78781F120B081D112E10CF027AC7EB0C46584900148F01CC00287D001FB8000380810D0A")
