class StudentManagerError(Exception):
    pass


class StudentValidationError(StudentManagerError, ValueError):
    pass


class StudentNotFoundError(StudentManagerError, ValueError):
    pass


class GroupNotFoundError(StudentManagerError, ValueError):
    pass


class DuplicateGroupError(StudentManagerError):
    pass


class DuplicateEmailError(StudentManagerError):
    pass


class GroupNotEmptyError(StudentManagerError):
    pass


class SchemaMissingError(StudentManagerError):
    pass
