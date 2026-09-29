"""Instagram / social discovery layer for EventTrakr."""

__all__ = ["poll_due_accounts", "poll_account"]


def __getattr__(name: str):
    if name in ("poll_due_accounts", "poll_account"):
        from app.services.social.poll import poll_account, poll_due_accounts

        return poll_due_accounts if name == "poll_due_accounts" else poll_account
    raise AttributeError(name)
