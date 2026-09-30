from collections.abc import Callable


class PreviewCancelled(Exception):
    """Raised only at safe boundaries; the owner cleans the temporary session."""


Checkpoint = Callable[[], None]


def no_checkpoint() -> None:
    pass
