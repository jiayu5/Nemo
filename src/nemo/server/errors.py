class SessionNotFoundError(LookupError):
    pass


class RunNotFoundError(LookupError):
    pass


class ApprovalNotFoundError(LookupError):
    pass


class ApprovalConflictError(RuntimeError):
    pass
