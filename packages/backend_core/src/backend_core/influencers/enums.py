"""Platform-neutral influencer identity enums."""

from enum import StrEnum


class Platform(StrEnum):
    XIAOHONGSHU = "xiaohongshu"


class DataSource(StrEnum):
    HUITUN = "huitun"
    GENERIC = "generic"
    MANUAL = "manual"


class InfluencerStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class CRMStage(StrEnum):
    TO_DEVELOP = "待开发"
    EMAIL_SENT = "已发送邮件"
    FIRST_FOLLOW_UP = "第一次跟进"
    SECOND_FOLLOW_UP = "第二次跟进"
    REPLIED = "已回复"
    WECHAT_ADDED = "已加微信"
    COMMUNICATING = "沟通中"
    POTENTIAL_COOPERATION = "潜在合作"
    HIGH_INTENT = "高意向"
    NOT_CONSIDERING = "暂不考虑"
    LONG_TERM_MAINTENANCE = "长期维护"
    CLOSED = "已结束"


class ContactType(StrEnum):
    EMAIL = "email"
    WECHAT = "wechat"
    PHONE = "phone"
    OTHER = "other"


class ContactValidationStatus(StrEnum):
    VALID = "valid"
    INVALID = "invalid"
    UNVERIFIED = "unverified"
