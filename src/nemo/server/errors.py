class SessionNotFoundError(LookupError):
    pass


class RunNotFoundError(LookupError):
    pass


class ActiveRunError(RuntimeError):
    pass


class ApprovalNotFoundError(LookupError):
    pass


class ApprovalConflictError(RuntimeError):
    pass


class ProviderNotFoundError(LookupError):
    pass
