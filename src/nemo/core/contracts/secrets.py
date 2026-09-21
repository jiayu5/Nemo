"""A wrapper that keeps credentials out of logs, traces and error messages."""

_MIN_REDACTABLE_LENGTH = 8


class SecretValue:
    """Holds a secret; ``repr``/``str`` are redacted by construction."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("Secret value must not be empty")
        self._value = value

    def reveal(self) -> str:
        """Return the raw value. Call sites must be places that cannot be logged."""

        return self._value

    def redact(self, text: str) -> str:
        """Replace occurrences of the secret inside ``text`` with ``***``.

        Very short values are left alone: redacting a 3-character secret would
        corrupt unrelated text without providing real protection.
        """

        if len(self._value) < _MIN_REDACTABLE_LENGTH or self._value not in text:
            return text
        return text.replace(self._value, "***")

    def __repr__(self) -> str:
        return "SecretValue('***')"

    __str__ = __repr__
