import math
import re

from student_manager.exceptions import StudentValidationError

GRADE_MIN = 0.0
GRADE_MAX = 100.0
NAME_MAX_LENGTH = 100
GROUP_PATTERN = re.compile(r"[A-ZА-ЯІЇЄҐ]{2,5}-[0-9]{2}")
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def clean_name(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StudentValidationError(f"{field}: потрібен непорожній рядок, отримано {value!r}")
    text = value.strip()
    if len(text) > NAME_MAX_LENGTH:
        raise StudentValidationError(f"{field}: не більше {NAME_MAX_LENGTH} символів")
    return text


def clean_grade(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise StudentValidationError(f"середній бал має бути числом, отримано {value!r}")
    grade = float(value)
    if math.isnan(grade) or not GRADE_MIN <= grade <= GRADE_MAX:
        raise StudentValidationError(f"середній бал {value!r} поза діапазоном 0–100")
    return grade


def clean_group_name(value: object) -> str:
    text = clean_name(value, "група")
    if not GROUP_PATTERN.fullmatch(text):
        raise StudentValidationError(f"некоректна назва групи {text!r}, очікується як PI-21")
    return text


def clean_email(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not EMAIL_PATTERN.fullmatch(value.strip()):
        raise StudentValidationError(f"некоректна адреса електронної пошти {value!r}")
    return value.strip().lower()
