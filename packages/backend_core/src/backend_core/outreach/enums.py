"""Closed persistence enums for Phase 3A outreach aggregates."""

from enum import StrEnum


class OutreachChannel(StrEnum):
    EMAIL = "EMAIL"
    XIAOHONGSHU_PRIVATE_MESSAGE = "XIAOHONGSHU_PRIVATE_MESSAGE"
    DOUYIN_PRIVATE_MESSAGE = "DOUYIN_PRIVATE_MESSAGE"
    WECHAT = "WECHAT"
    MANUAL = "MANUAL"


class OutreachTaskKind(StrEnum):
    FIRST_TOUCH = "FIRST_TOUCH"
    FOLLOW_UP = "FOLLOW_UP"


class OutreachTaskState(StrEnum):
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    READY = "READY"
    SENT = "SENT"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class OutreachPriority(StrEnum):
    NORMAL = "NORMAL"
    HIGH = "HIGH"


class OutreachPrioritySource(StrEnum):
    DEFAULT = "DEFAULT"
    MANUAL = "MANUAL"
    POLICY = "POLICY"


class OutreachEventType(StrEnum):
    TASK_CREATED = "TASK_CREATED"
    REVIEW_APPROVED = "REVIEW_APPROVED"
    OUTREACH_SENT = "OUTREACH_SENT"
    OUTREACH_FAILED = "OUTREACH_FAILED"
    OUTREACH_STOPPED = "OUTREACH_STOPPED"
    OUTREACH_RETRIED = "OUTREACH_RETRIED"


class OutreachActorType(StrEnum):
    OPERATOR = "OPERATOR"
    SYSTEM = "SYSTEM"


class MessageTemplateState(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
